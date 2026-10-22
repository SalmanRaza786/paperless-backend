from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.config import settings
from app.core.security import hash_token, create_access_token
from app.modules.auth.models import User, UserSession, AuthAuditLog, RolePermission, Permission
from app.modules.auth.schemas import (
    LoginRequest,
    AuthTokenResponse,
    UserResponse,
    TenantSummaryResponse,
    RefreshTokenRequest,
    LogoutRequest,
    SelfRegisterRequest,
    RegisterInitiatedResponse,
    VerifyRegistrationOtpRequest,
    ResendOtpRequest,
    ResendOtpResponse,
)
from app.modules.auth.services import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication & Accounts"])
security = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    """Extract client real IP handling proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency for authenticating endpoints via Bearer token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide a valid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await AuthService.get_current_user_from_token(db, credentials.credentials)


async def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Optional authentication dependency that does not raise if token is missing/expired."""
    if not credentials or not credentials.credentials:
        return None
    try:
        return await AuthService.get_current_user_from_token(db, credentials.credentials)
    except HTTPException:
        return None


@router.post("/login", response_model=AuthTokenResponse, summary="User Login (Email or Phone)")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate user with Email or Phone and Password.
    - **Super Admin**: Bypasses any OTP requirements directly.
    - **Brute-force protection**: 5 failed attempts trigger 15-minute lockout.
    - **Multi-tenancy**: Returns current tenant and all available tenant workspaces.
    - **Cookie**: Automatically sets paperless_access_token HTTP cookie for Next.js middleware.
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "Unknown")

    auth_response = await AuthService.authenticate_user(
        db=db,
        login_data=payload,
        client_ip=client_ip,
        user_agent=user_agent,
    )

    # Set cookie in response for server-side Next.js middleware
    response.set_cookie(
        key="paperless_access_token",
        value=auth_response.access_token,
        max_age=auth_response.expires_in,
        path="/",
        samesite="lax",
        httponly=False, # Accessible to JS client for auth-guard checks
    )

    return auth_response


@router.get("/me", response_model=UserResponse, summary="Get Current Authenticated User")
async def get_me(current_user: User = Depends(get_current_user)):
    """Return profile details of the currently authenticated user including active business."""
    available_tenants = []
    current_tenant = None

    for m in current_user.memberships:
        if m.is_active and m.tenant:
            if current_user.is_superadmin or m.is_owner or (m.role and m.role.name == "Admin"):
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
            if current_user.default_tenant_id == m.tenant.id or current_tenant is None:
                current_tenant = t_summary

    resp = UserResponse.model_validate(current_user)
    resp.current_tenant = current_tenant
    resp.available_tenants = available_tenants
    return resp


@router.post("/refresh", summary="Rotate JWT Access Token using Refresh Token")
async def refresh_token(
    payload: RefreshTokenRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Generate a new access token using an active, unrevoked refresh token."""
    now = datetime.now(timezone.utc)
    t_hash = hash_token(payload.refresh_token)

    stmt = select(UserSession).where(
        UserSession.token_hash == t_hash,
        UserSession.is_revoked.is_(False),
        UserSession.expires_at > now,
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token. Please login again.",
        )

    # Load user
    stmt_u = select(User).where(User.id == session.user_id, User.is_active.is_(True))
    u_res = await db.execute(stmt_u)
    user = u_res.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account no longer active.",
        )

    # Create new access token
    new_access_token = create_access_token(
        subject=user.id,
        claims={
            "email": user.email,
            "full_name": user.full_name,
            "is_superadmin": user.is_superadmin,
            "tenant_id": session.current_tenant_id,
        },
    )

    session.last_used_at = now
    await db.commit()

    # Update cookie
    response.set_cookie(
        key="paperless_access_token",
        value=new_access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        samesite="lax",
        httponly=False,
    )

    return {
        "access_token": new_access_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@router.post("/logout", summary="Logout and Revoke Session")
async def logout(
    response: Response,
    payload: Optional[LogoutRequest] = None,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Revoke refresh token and invalidate user session.
    Works whether access token is valid or expired.
    Deletes server cookie as well.
    """
    user_id = current_user.id if current_user else None
    user_email = current_user.email if current_user else None

    if payload and payload.refresh_token:
        t_hash = hash_token(payload.refresh_token)
        stmt = select(UserSession).where(UserSession.token_hash == t_hash)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if session:
            session.is_revoked = True
            if not user_id:
                user_id = session.user_id

    # Record in audit log
    audit = AuthAuditLog(
        user_id=user_id,
        event="LOGOUT",
        metadata_={"user_email": user_email},
    )
    db.add(audit)
    await db.commit()

    # Clear cookie in HTTP response
    response.delete_cookie(
        key="paperless_access_token",
        path="/",
        samesite="lax",
    )

    return {
        "success": True,
        "message": "Successfully logged out and session revoked.",
    }


@router.post(
    "/register",
    response_model=RegisterInitiatedResponse,
    summary="Public Business Self-Registration (Sends Email OTP)",
)
async def register(
    payload: SelfRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Public self-service merchant signup.
    Saves business & owner details, generates a 6-digit OTP, and sends it to the owner's email.
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "Unknown")

    return await AuthService.initiate_self_registration(
        db=db,
        payload=payload,
        client_ip=client_ip,
        user_agent=user_agent,
    )


@router.post(
    "/verify-registration-otp",
    response_model=AuthTokenResponse,
    summary="Verify Email OTP & Provision Tenant + Owner",
)
async def verify_registration_otp(
    payload: VerifyRegistrationOtpRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify 6-digit OTP code.
    Atomically creates Tenant, Owner User, Role membership, sets auth cookie, and logs in.
    """
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("user-agent", "Unknown")

    auth_response = await AuthService.verify_registration_otp(
        db=db,
        payload=payload,
        client_ip=client_ip,
        user_agent=user_agent,
    )

    # Set cookie in response for server-side Next.js middleware
    response.set_cookie(
        key="paperless_access_token",
        value=auth_response.access_token,
        max_age=auth_response.expires_in,
        path="/",
        samesite="lax",
        httponly=False,
    )

    return auth_response


@router.post(
    "/resend-otp",
    response_model=ResendOtpResponse,
    summary="Resend Verification OTP (60s Cooldown)",
)
async def resend_otp(
    payload: ResendOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resend a fresh 6-digit code to the registered email."""
    return await AuthService.resend_registration_otp(db=db, payload=payload)

