"""Database seeder script for initial system data, permissions matrix, and default Super Admin."""
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.core.security import hash_password
from app.modules.auth.models import (
    User,
    Tenant,
    Role,
    TenantUser,
    Permission,
    RolePermission,
    TenantStatus,
    SubscriptionTier,
    OTPChannel,
)


MASTER_PERMISSIONS = [
    # Users & Roles (Active Module)
    {"module": "users", "code": "users:view", "name": "View Users", "description": "View tenant staff directory and activity logs"},
    {"module": "users", "code": "users:manage", "name": "Manage Users", "description": "Add, edit, and deactivate staff user accounts"},
    {"module": "users", "code": "roles:manage", "name": "Manage Roles & Matrix", "description": "Configure roles and permission matrices"},
]


# System global roles
ROLE_ASSIGNMENTS = {
    "Super Admin": ["*"],  # Global platform administrator
}


async def seed_database():
    print("--- Starting Database Seeder (Permissions & Roles) ---")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        # 1. Seed Permissions Catalog
        print("[1/4] Seeding Master Permissions...")
        perm_map = {}
        for p_data in MASTER_PERMISSIONS:
            stmt = select(Permission).where(Permission.code == p_data["code"])
            res = await session.execute(stmt)
            existing_perm = res.scalar_one_or_none()

            if not existing_perm:
                new_perm = Permission(
                    module=p_data["module"],
                    code=p_data["code"],
                    name=p_data["name"],
                    description=p_data["description"],
                    created_at=now,
                )
                session.add(new_perm)
                await session.flush()
                perm_map[p_data["code"]] = new_perm
                print(f"  + Added Permission: {p_data['code']}")
            else:
                perm_map[p_data["code"]] = existing_perm

        # 2. Seed System Roles
        print("[2/4] Seeding System Roles...")
        role_map = {}
        for role_name, _ in ROLE_ASSIGNMENTS.items():
            stmt = select(Role).where(Role.name == role_name, Role.tenant_id.is_(None))
            res = await session.execute(stmt)
            existing_role = res.scalar_one_or_none()

            if not existing_role:
                new_role = Role(
                    tenant_id=None,
                    name=role_name,
                    description=f"System default role for {role_name}",
                    is_system=True,
                    permissions={},
                    created_at=now,
                )
                session.add(new_role)
                await session.flush()
                role_map[role_name] = new_role
                print(f"  + Added Role: {role_name}")
            else:
                role_map[role_name] = existing_role

        # 3. Seed Role Permissions Mapping
        print("[3/4] Mapping Permissions to Roles in `role_permissions`...")
        all_perms = list(perm_map.values())
        created_rp_count = 0

        for role_name, codes in ROLE_ASSIGNMENTS.items():
            role_obj = role_map[role_name]
            target_perms = all_perms if "*" in codes else [perm_map[c] for c in codes if c in perm_map]

            for p in target_perms:
                stmt_rp = select(RolePermission).where(
                    RolePermission.role_id == role_obj.id,
                    RolePermission.permission_id == p.id,
                )
                res_rp = await session.execute(stmt_rp)
                if not res_rp.scalar_one_or_none():
                    rp = RolePermission(
                        role_id=role_obj.id,
                        permission_id=p.id,
                        created_at=now,
                    )
                    session.add(rp)
                    created_rp_count += 1

        await session.flush()
        print(f"  + Linked {created_rp_count} new role-permission entries.")

        # 4. Seed Default Demo Tenant & Super Admin
        print("[4/4] Verifying Demo Tenant & Super Admin...")
        stmt_t = select(Tenant).where(Tenant.slug == "paperless-demo")
        res_t = await session.execute(stmt_t)
        demo_tenant = res_t.scalar_one_or_none()

        if not demo_tenant:
            demo_tenant = Tenant(
                slug="paperless-demo",
                name="Paperless OS",
                legal_name="Paperless Business OS Enterprise Ltd.",
                phone="+923001234567",
                email="contact@paperless.pk",
                city="Lahore",
                currency="PKR",
                status=TenantStatus.ACTIVE,
                plan_tier=SubscriptionTier.ENTERPRISE,
                trial_ends_at=now + timedelta(days=365),
                is_read_only=False,
                created_at=now,
            )
            session.add(demo_tenant)
            await session.flush()

        stmt_u = select(User).where(User.email == "superadmin@gmail.com")
        res_u = await session.execute(stmt_u)
        superadmin_user = res_u.scalar_one_or_none()

        if not superadmin_user:
            superadmin_user = User(
                full_name="Super Administrator",
                email="superadmin@gmail.com",
                phone="+923000000000",
                password_hash=hash_password("12345678"),
                is_phone_verified=True,
                is_email_verified=True,
                is_active=True,
                is_superadmin=True,
                preferred_otp_channel=OTPChannel.EMAIL,
                default_tenant_id=demo_tenant.id,
                created_at=now,
            )
            session.add(superadmin_user)
            await session.flush()

        await session.commit()
        print("\n--- Permissions and Roles Seeding Completed Successfully! ---")
        print(f"Total Master Permissions: {len(MASTER_PERMISSIONS)}")
        print(f"Total Roles Configured:   {len(ROLE_ASSIGNMENTS)}")


if __name__ == "__main__":
    asyncio.run(seed_database())
