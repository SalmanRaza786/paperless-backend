import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any
from fastapi import HTTPException, status, Request
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    verify_password,
    hash_password,
    create_access_token,
    create_refresh_token,
    hash_token,
    decode_access_token,
)
from app.modules.auth.models import (
    User,
    Tenant,
    TenantUser,
    Role,
    RolePermission,
    Permission,
    UserSession,
    AuthAuditLog,
    OTPVerification,
    OTPPurpose,
    OTPChannel,
    OTPDeliveryStatus,
    TenantStatus,
    SubscriptionTier,
)
from app.modules.auth.schemas import (
    LoginRequest,
    AuthTokenResponse,
    UserResponse,
    TenantSummaryResponse,
    SelfRegisterRequest,
    RegisterInitiatedResponse,
    VerifyRegistrationOtpRequest,
    ResendOtpRequest,
    ResendOtpResponse,
)
from app.modules.team.services import TeamService


def slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s[:45] if s else "business"



class AuthService:
    @staticmethod
    async def authenticate_user(
        db: AsyncSession,
        login_data: LoginRequest,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthTokenResponse:
        """
        Authenticate user with Email or Phone and password.
        Super Admin skips OTP verification completely.
        Includes brute-force protection and audit logging.
        """
        now = datetime.now(timezone.utc)
        ident = login_data.identifier.strip()

        # 1. Lookup user by email or phone
        stmt = (
            select(User)
            .where(or_(User.email == ident, User.phone == ident))
            .options(
                selectinload(User.memberships).joinedload(TenantUser.tenant),
                selectinload(User.memberships)
                .selectinload(TenantUser.role)
                .selectinload(Role.role_permissions)
                .joinedload(RolePermission.permission),
            )
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            # Audit log failed attempt with non-existent user
            audit = AuthAuditLog(
                event="LOGIN_FAILED_UNKNOWN_USER",
                ip_address=client_ip,
                user_agent=user_agent,
                metadata_={"identifier": ident},
            )
            db.add(audit)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials. Please check your email/phone and password.",
            )

        # 2. Check if account is locked due to brute-force protection
        if user.locked_until and user.locked_until > now:
            remaining_mins = int((user.locked_until - now).total_seconds() / 60) + 1
            audit = AuthAuditLog(
                user_id=user.id,
                event="LOGIN_BLOCKED_LOCKED",
                ip_address=client_ip,
                user_agent=user_agent,
            )
            db.add(audit)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Account is temporarily locked due to multiple failed attempts. Please try again in {remaining_mins} minutes.",
            )

        # 3. Check if account is active
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account has been deactivated. Please contact support.",
            )

        # 4. Verify password
        is_valid_pwd = verify_password(login_data.password, user.password_hash)

        if not is_valid_pwd:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= 5:
                user.locked_until = now + timedelta(minutes=15)

            audit = AuthAuditLog(
                user_id=user.id,
                event="LOGIN_FAILED_WRONG_PASSWORD",
                ip_address=client_ip,
                user_agent=user_agent,
                metadata_={"failed_attempts": user.failed_login_attempts},
            )
            db.add(audit)
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials. Please check your email/phone and password.",
            )

        # 5. Password is valid -> Reset lockout counters
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        user.last_login_ip = client_ip

        # 6. Check Super Admin rule:
        # Super Admin logins directly without requiring OTP.
        # (Regular users can have OTP challenges when 2FA is active).

        # 7. Resolve Current & Available Tenants
        available_tenants = []
        current_tenant = None

        for m in user.memberships:
            if m.is_active and m.tenant:
                if user.is_superadmin or m.is_owner or (m.role and m.role.name == "Admin"):
                    perms = ["*"]
                elif m.role and hasattr(m.role, "role_permissions"):
                    perms = [rp.permission.code for rp in m.role.role_permissions if rp.permission]
                else:
                    perms = []

                t_summary = TenantSummaryResponse(
                    id=m.tenant.id,
                    name=m.tenant.name,
                    slug=m.tenant.slug,
                    plan_tier=m.tenant.plan_tier,
                    is_read_only=m.tenant.is_read_only,
                    role_name=m.role.name if m.role else None,
                    permissions=perms,
                )
                available_tenants.append(t_summary)
                if user.default_tenant_id == m.tenant.id or current_tenant is None:
                    current_tenant = t_summary

        # If user has no explicit tenant yet (e.g. fresh standalone Super Admin), look up demo tenant
        if not current_tenant and user.is_superadmin:
            stmt_t = select(Tenant).order_by(Tenant.id.asc()).limit(1)
            t_res = await db.execute(stmt_t)
            first_tenant = t_res.scalar_one_or_none()
            if first_tenant:
                current_tenant = TenantSummaryResponse(
                    id=first_tenant.id,
                    name=first_tenant.name,
                    slug=first_tenant.slug,
                    plan_tier=first_tenant.plan_tier,
                    is_read_only=first_tenant.is_read_only,
                    role_name="Super Admin",
                    permissions=["*"],
                )
                available_tenants.append(current_tenant)

        # 8. Generate JWT Access Token
        access_token_claims = {
            "email": user.email,
            "full_name": user.full_name,
            "is_superadmin": user.is_superadmin,
            "tenant_id": current_tenant.id if current_tenant else None,
            "role": current_tenant.role_name if current_tenant else "User",
        }
        access_token = create_access_token(
            subject=user.id,
            claims=access_token_claims,
            expires_delta=timedelta(days=1) if login_data.remember_me else None,
        )

        # 9. Generate and store Refresh Token
        raw_refresh_token, token_hash = create_refresh_token()
        refresh_expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

        session_record = UserSession(
            user_id=user.id,
            current_tenant_id=current_tenant.id if current_tenant else None,
            token_hash=token_hash,
            device_name=user_agent[:150] if user_agent else "Web Browser",
            ip_address=client_ip,
            is_revoked=False,
            expires_at=refresh_expires_at,
            last_used_at=now,
        )
        db.add(session_record)

        # 10. Audit log login success
        audit = AuthAuditLog(
            user_id=user.id,
            tenant_id=current_tenant.id if current_tenant else None,
            event="LOGIN_SUCCESS",
            ip_address=client_ip,
            user_agent=user_agent,
            metadata_={"is_superadmin": user.is_superadmin, "remember_me": login_data.remember_me},
        )
        db.add(audit)

        await db.commit()

        user_response = UserResponse.model_validate(user)

        return AuthTokenResponse(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=user_response,
            current_tenant=current_tenant,
            available_tenants=available_tenants,
        )

    @staticmethod
    async def get_current_user_from_token(
        db: AsyncSession,
        token: str,
    ) -> User:
        """Decode and load user from Bearer JWT access token."""
        payload = decode_access_token(token)
        if not payload or not payload.get("sub"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired access token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = int(payload["sub"])
        stmt = (
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.memberships).joinedload(TenantUser.tenant),
                selectinload(User.memberships)
                .selectinload(TenantUser.role)
                .selectinload(Role.role_permissions)
                .joinedload(RolePermission.permission),
            )
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user

    @staticmethod
    async def initiate_self_registration(
        db: AsyncSession,
        payload: SelfRegisterRequest,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> RegisterInitiatedResponse:
        """
        Public self-service business registration initiation.
        Validates uniqueness of owner email/phone, generates a 6-digit Email OTP,
        and saves pending registration state in otp_verifications table.
        """
        now = datetime.now(timezone.utc)
        owner_email = payload.owner_email.strip().lower()
        owner_phone = payload.owner_phone.strip()

        # 1. Check if user with this email or phone already exists
        stmt = select(User).where(or_(User.email == owner_email, User.phone == owner_phone))
        existing_user = (await db.execute(stmt)).scalar_one_or_none()
        if existing_user:
            if existing_user.email == owner_email:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this email address already exists. Please log in instead.",
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this mobile phone number already exists. Please log in instead.",
                )

        # 2. Generate 6-digit numeric OTP
        otp_code = f"{secrets.randbelow(900000) + 100000}"
        otp_hash = hash_token(otp_code)

        # 3. Store in OTPVerification table
        verification = OTPVerification(
            user_id=None,
            destination=owner_email,
            channel=OTPChannel.EMAIL,
            purpose=OTPPurpose.SIGNUP,
            otp_hash=otp_hash,
            attempts=0,
            max_attempts=5,
            expires_at=now + timedelta(minutes=15),
            resend_available_at=now + timedelta(seconds=60),
            is_verified=False,
            delivery_status=OTPDeliveryStatus.SENT,
            delivery_provider="console_email",
            delivery_response=payload.model_dump(mode="json"),
            ip_address=client_ip,
            user_agent=user_agent,
            created_at=now,
        )
        db.add(verification)
        await db.commit()
        await db.refresh(verification)

        # 4. In development, log the OTP clearly in terminal
        print("\n" + "=" * 65)
        print(f"📧 [PAPERLESS OTP DISPATCHER]")
        print(f"   Destination: {owner_email}")
        print(f"   Purpose:     NEW BUSINESS REGISTRATION")
        print(f"   VERIFICATION CODE: >>> {otp_code} <<<")
        print(f"   Valid for:   15 minutes")
        print("=" * 65 + "\n")

        return RegisterInitiatedResponse(
            success=True,
            verification_id=verification.id,
            destination=owner_email,
            channel=OTPChannel.EMAIL,
            resend_available_in=60,
            message=f"A 6-digit verification code has been sent to {owner_email}.",
            debug_otp=otp_code,
        )

    @staticmethod
    async def verify_registration_otp(
        db: AsyncSession,
        payload: VerifyRegistrationOtpRequest,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthTokenResponse:
        """
        Verify the 6-digit OTP code and atomically provision the new Tenant,
        the Owner User account, and assign the Owner system role.
        """
        now = datetime.now(timezone.utc)

        stmt = select(OTPVerification).where(OTPVerification.id == payload.verification_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Verification request not found or has expired.",
            )

        if record.is_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This verification code has already been used.",
            )

        if record.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verification code has expired. Please request a new code.",
            )

        if record.attempts >= record.max_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many incorrect attempts. Please request a new verification code.",
            )

        # Verify OTP Hash
        input_hash = hash_token(payload.otp_code.strip())
        if input_hash != record.otp_hash:
            record.attempts += 1
            await db.commit()
            remaining = record.max_attempts - record.attempts
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid verification code. {remaining} attempt(s) remaining.",
            )

        # OTP is VALID! Mark verified
        record.is_verified = True
        record.verified_at = now

        reg_data = record.delivery_response or {}
        business_name = reg_data.get("business_name", "My Business")
        owner_email = reg_data.get("owner_email", record.destination).strip().lower()
        owner_phone = reg_data.get("owner_phone", "").strip()
        owner_name = reg_data.get("owner_full_name", "").strip()
        owner_password = reg_data.get("owner_password", "")
        plan_tier = reg_data.get("plan_tier", "free_trial")

        # 1. Generate Unique Slug for Tenant
        base_slug = slugify(business_name)
        slug = base_slug
        counter = 1
        while True:
            t_check = await db.execute(select(Tenant).where(Tenant.slug == slug))
            if not t_check.scalar_one_or_none():
                break
            counter += 1
            slug = f"{base_slug}-{counter}"

        # 2. Create Tenant
        tenant = Tenant(
            slug=slug,
            name=business_name,
            legal_name=reg_data.get("legal_name"),
            ntn=reg_data.get("ntn"),
            strn=reg_data.get("strn"),
            phone=reg_data.get("phone", owner_phone),
            email=owner_email,
            city=reg_data.get("city", "Lahore"),
            address=reg_data.get("address"),
            currency="PKR",
            status=TenantStatus.TRIAL,
            plan_tier=SubscriptionTier(plan_tier) if plan_tier in SubscriptionTier._value2member_map_ else SubscriptionTier.FREE_TRIAL,
            trial_ends_at=now + timedelta(days=14),
            subscription_ends_at=now + timedelta(days=14),
            is_read_only=False,
            created_at=now,
        )
        db.add(tenant)
        await db.flush()

        # 3. Create User
        user = User(
            full_name=owner_name,
            phone=owner_phone if owner_phone else None,
            email=owner_email,
            password_hash=hash_password(owner_password),
            is_phone_verified=False,
            is_email_verified=True,
            is_active=True,
            is_superadmin=False,
            preferred_otp_channel=OTPChannel.EMAIL,
            default_tenant_id=tenant.id,
            created_at=now,
        )
        db.add(user)
        await db.flush()

        # 4. Provision Dedicated Tenant Roles and assign user to Admin role
        admin_role = await TeamService.seed_tenant_default_roles(db, tenant.id)

        membership = TenantUser(
            tenant_id=tenant.id,
            user_id=user.id,
            role_id=admin_role.id,
            is_owner=True,
            is_active=True,
            max_discount_pct=100.0,
            joined_at=now,
        )
        db.add(membership)

        # 5. Record in Audit Log
        audit = AuthAuditLog(
            user_id=user.id,
            tenant_id=tenant.id,
            event="SELF_REGISTRATION_VERIFIED",
            ip_address=client_ip,
            user_agent=user_agent,
            metadata_={"business_name": business_name, "plan_tier": plan_tier},
        )
        db.add(audit)

        # 6. Generate JWT Access and Refresh Tokens
        current_tenant = TenantSummaryResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            plan_tier=tenant.plan_tier,
            is_read_only=tenant.is_read_only,
            role_name="Owner",
        )

        access_token_claims = {
            "email": user.email,
            "full_name": user.full_name,
            "is_superadmin": False,
            "tenant_id": tenant.id,
            "role": "Owner",
        }
        access_token = create_access_token(
            subject=user.id,
            claims=access_token_claims,
            expires_delta=timedelta(days=7),
        )

        raw_refresh_token, token_hash = create_refresh_token()
        session_record = UserSession(
            user_id=user.id,
            current_tenant_id=tenant.id,
            token_hash=token_hash,
            device_name=user_agent[:150] if user_agent else "Web Browser",
            ip_address=client_ip,
            is_revoked=False,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            last_used_at=now,
        )
        db.add(session_record)

        await db.commit()

        user_response = UserResponse.model_validate(user)

        return AuthTokenResponse(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            user=user_response,
            current_tenant=current_tenant,
            available_tenants=[current_tenant],
        )

    @staticmethod
    async def resend_registration_otp(
        db: AsyncSession,
        payload: ResendOtpRequest,
    ) -> ResendOtpResponse:
        """Resend a fresh 6-digit OTP code with a 60-second cooldown."""
        now = datetime.now(timezone.utc)

        stmt = select(OTPVerification).where(OTPVerification.id == payload.verification_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if not record or record.is_verified:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Verification session expired or already verified.",
            )

        if record.resend_available_at and record.resend_available_at > now:
            wait_seconds = int((record.resend_available_at - now).total_seconds()) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {wait_seconds} seconds before requesting a new verification code.",
            )

        new_otp_code = f"{secrets.randbelow(900000) + 100000}"
        record.otp_hash = hash_token(new_otp_code)
        record.attempts = 0
        record.expires_at = now + timedelta(minutes=15)
        record.resend_available_at = now + timedelta(seconds=60)
        record.delivery_status = OTPDeliveryStatus.SENT

        await db.commit()

        print("\n" + "=" * 65)
        print(f"📧 [PAPERLESS RESEND OTP]")
        print(f"   Destination: {record.destination}")
        print(f"   NEW VERIFICATION CODE: >>> {new_otp_code} <<<")
        print(f"   Valid for:   15 minutes")
        print("=" * 65 + "\n")

        return ResendOtpResponse(
            success=True,
            verification_id=record.id,
            destination=record.destination,
            channel=record.channel,
            resend_available_in=60,
            message=f"A fresh verification code has been sent to {record.destination}.",
            debug_otp=new_otp_code,
        )

