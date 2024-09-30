from typing import Optional, Type, TypeVar

from gsuid_core.utils.database.base_models import (
    Bind,
    Push,
    User,
    with_session,
)
from gsuid_core.webconsole.mount_app import GsAdminModel, PageSchema, site
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Field, SQLModel, select

T_MajsPaipu = TypeVar("T_MajsPaipu", bound="MajsPaipu")


class MajsPaipu(SQLModel, table=True):
    uuid: str = Field(default="", primary_key=True, title="牌谱UUID")
    account_id: str = Field(default="", title="雀魂账号ID")

    @classmethod
    @with_session
    async def insert_data(
        cls: Type[T_MajsPaipu], session: AsyncSession, uuid: str, account_id: str
    ) -> int:
        data = cls(uuid=uuid, account_id=account_id)
        session.add(data)
        return 0

    @classmethod
    @with_session
    async def data_exist(
        cls: Type[T_MajsPaipu], session: AsyncSession, uuid: str
    ) -> bool:
        stmt = select(cls).where(cls.uuid == uuid)
        result = await session.execute(stmt)
        data = result.scalars().all()
        return bool(data)


class MajsPush(Push, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")
    user_id: str = Field(default="", title="用户ID")
    push_id: str = Field(default="off", title="是否开启推送")


class MajsBind(Bind, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")


class MajsUser(User, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")
    friend_code: str = Field(default="", title="好友码")
    account: str = Field(default="", title="账号")
    password: str = Field(default="", title="密码")

    @classmethod
    @with_session
    async def get_account(
        cls: Type["MajsUser"],
        session: AsyncSession,
        uid: str,
    ) -> Optional[str]:
        return await cls.get_user_attr_by_uid(uid, "account")

    @classmethod
    @with_session
    async def get_password(
        cls: Type["MajsUser"],
        session: AsyncSession,
        uid: str,
    ) -> Optional[str]:
        return await cls.get_user_attr_by_uid(uid, "password")


@site.register_admin
class MajsPaipuadmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="雀魂牌谱管理",
        icon="fa fa-users",
    )  # type: ignore

    # 配置管理模型
    model = MajsPaipu


@site.register_admin
class MajsBindadmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="雀魂绑定管理",
        icon="fa fa-users",
    )  # type: ignore

    # 配置管理模型
    model = MajsBind


@site.register_admin
class MajsUseradmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="雀魂账号池管理",
        icon="fa fa-users",
    )  # type: ignore

    # 配置管理模型
    model = MajsUser


@site.register_admin
class MajsPushadmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="雀魂推送管理",
        icon="fa fa-users",
    )  # type: ignore

    # 配置管理模型
    model = MajsPush
