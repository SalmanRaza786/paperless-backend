"""Database reset and tenant-isolated roles migration script."""
import asyncio
from datetime import datetime, timezone
from sqlalchemy import select, delete, text
from app.db.session import AsyncSessionLocal
from app.modules.auth.models import (
    Permission,
    Role,
    RolePermission,
    UserPermissionOverride,
    Tenant,
    TenantUser,
    User,
)

CLEAN_MASTER_PERMISSIONS = [
    # Users Module
    {"module": "users", "code": "users:view", "name": "View Users", "description": "View tenant staff directory and member profiles"},
    {"module": "users", "code": "users:create", "name": "Add Users", "description": "Create and invite new staff user accounts"},
    {"module": "users", "code": "users:edit", "name": "Edit Users", "description": "Update staff user profiles, roles, and active status"},
    {"module": "users", "code": "users:delete", "name": "Delete Users", "description": "Deactivate or remove staff accounts from the business"},

    # Roles Module
    {"module": "roles", "code": "roles:view", "name": "View Roles & Permissions", "description": "View business roles and permissions matrix"},
    {"module": "roles", "code": "roles:create", "name": "Create Roles", "description": "Create new custom business roles"},
    {"module": "roles", "code": "roles:edit", "name": "Edit Roles & Matrix", "description": "Customize role permissions and matrix configurations"},
    {"module": "roles", "code": "roles:delete", "name": "Delete Roles", "description": "Delete custom business roles"},

    # Branches Module
    {"module": "branches", "code": "branches:view", "name": "View Shops & Branches", "description": "View business shops, warehouses, and branch quota status"},
    {"module": "branches", "code": "branches:create", "name": "Create Shops & Branches", "description": "Add new retail shops, warehouses, or godowns"},
    {"module": "branches", "code": "branches:edit", "name": "Edit Shops & Branches", "description": "Update branch contact info, location details, and status"},
    {"module": "branches", "code": "branches:delete", "name": "Delete Shops & Branches", "description": "Remove branch locations from the business network"},
]


TENANT_DEFAULT_ROLE_TEMPLATES = {
    "Admin": {
        "description": "Business owner and full operations administrator with all permissions.",
        "permissions": ["*"],  # All active permissions of available modules
    },
}


async def migrate_and_reset():
    now = datetime.now(timezone.utc)
    print("=== Starting Permissions Reset & Multi-Tenant Role Isolation ===")

    async with AsyncSessionLocal() as session:
        # Step 1: Clear existing permission overrides & role_permissions
        print("1. Clearing role_permissions and user_permission_overrides...")
        await session.execute(delete(UserPermissionOverride))
        await session.execute(delete(RolePermission))
        await session.flush()

        # Step 2: Clear and re-populate permissions table
        print("2. Resetting permissions master catalog...")
        await session.execute(delete(Permission))
        await session.flush()

        perm_map = {}
        for p_data in CLEAN_MASTER_PERMISSIONS:
            perm = Permission(
                module=p_data["module"],
                code=p_data["code"],
                name=p_data["name"],
                description=p_data["description"],
                created_at=now,
            )
            session.add(perm)
        await session.flush()

        # Build code-to-perm mapping
        all_perms_res = await session.execute(select(Permission))
        all_perms = all_perms_res.scalars().all()
        for p in all_perms:
            perm_map[p.code] = p

        print(f"   Created {len(perm_map)} master permissions.")

        # Step 3: Ensure Super Admin platform role exists (global)
        print("3. Ensuring global Super Admin role...")
        sa_res = await session.execute(select(Role).where(Role.name == "Super Admin", Role.tenant_id.is_(None)))
        super_admin_role = sa_res.scalar_one_or_none()
        if not super_admin_role:
            super_admin_role = Role(
                name="Super Admin",
                description="Platform super administrator with global system oversight.",
                is_system=True,
                tenant_id=None,
                created_at=now,
            )
            session.add(super_admin_role)
            await session.flush()

        for p in all_perms:
            session.add(RolePermission(role_id=super_admin_role.id, permission_id=p.id, created_at=now))
        await session.flush()

        # Step 4: Delete old global roles (like old global Owner, Admin, Cashier, etc.)
        # But first detach TenantUser to avoid FK constraint issues
        print("4. Updating TenantUser memberships to isolated per-tenant roles...")

        tenants_res = await session.execute(select(Tenant))
        tenants = tenants_res.scalars().all()

        for tenant in tenants:
            print(f"   Provisioning isolated roles for Tenant {tenant.id} ({tenant.name})...")

            # Check if tenant already has roles or create them
            tenant_roles_res = await session.execute(select(Role).where(Role.tenant_id == tenant.id))
            existing_tenant_roles = {r.name: r for r in tenant_roles_res.scalars().all()}

            tenant_role_map = {}
            for role_name, tpl in TENANT_DEFAULT_ROLE_TEMPLATES.items():
                if role_name in existing_tenant_roles:
                    r = existing_tenant_roles[role_name]
                else:
                    r = Role(
                        tenant_id=tenant.id,
                        name=role_name,
                        description=tpl["description"],
                        is_system=True,
                        created_at=now,
                    )
                    session.add(r)
                    await session.flush()

                tenant_role_map[role_name] = r

                # Assign permissions to this tenant's role
                if tpl["permissions"] == ["*"]:
                    for p in all_perms:
                        session.add(RolePermission(role_id=r.id, permission_id=p.id, created_at=now))
                else:
                    for code in tpl["permissions"]:
                        if code in perm_map:
                            session.add(RolePermission(role_id=r.id, permission_id=perm_map[code].id, created_at=now))

            await session.flush()

            # Now update all TenantUser rows for this tenant to point to this tenant's own roles!
            mems_res = await session.execute(select(TenantUser).where(TenantUser.tenant_id == tenant.id))
            memberships = mems_res.scalars().all()

            for mem in memberships:
                if mem.is_owner:
                    # Business owner is assigned the tenant's Admin role!
                    mem.role_id = tenant_role_map["Admin"].id
                else:
                    # If staff was Accountant, assign to tenant's Accountant role, etc.
                    # Default fallback to Cashier or Accountant
                    mem.role_id = tenant_role_map.get("Accountant", tenant_role_map["Cashier"]).id

            await session.flush()

        # Step 5: Clean up old global non-superadmin roles
        print("5. Removing outdated global template roles...")
        await session.execute(
            delete(Role).where(
                Role.tenant_id.is_(None),
                Role.name != "Super Admin",
            )
        )
        await session.commit()

        print("=== Multi-Tenant Permissions Reset Completed Successfully! ===")


if __name__ == "__main__":
    asyncio.run(migrate_and_reset())
