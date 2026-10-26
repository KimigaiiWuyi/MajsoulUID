from typing import Type, TypeVar, Optional

from sqlmodel import Field, col, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from gsuid_core.utils.database.models import CoreUser
from gsuid_core.utils.database.startup import exec_list
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site
from gsuid_core.utils.database.base_models import (
    Bind,
    Push,
    User,
    BaseIDModel,
    with_session,
    with_read_session,
)

T_MajsPaipu = TypeVar("T_MajsPaipu", bound="MajsPaipu")

exec_list.append('ALTER TABLE MajsUser ADD COLUMN username TEXT DEFAULT ""')
exec_list.append('ALTER TABLE MajsUser ADD COLUMN password TEXT DEFAULT ""')
exec_list.append('ALTER TABLE MajsUser ADD COLUMN account TEXT DEFAULT ""')

exec_list.append('ALTER TABLE MajsUser ADD COLUMN token TEXT DEFAULT ""')
exec_list.append('ALTER TABLE MajsUser ADD COLUMN lang TEXT DEFAULT "zh"')
exec_list.append("ALTER TABLE MajsUser ADD COLUMN login_type INT DEFAULT 0")


class MajsPaipu(BaseIDModel, table=True):
    account_id: str = Field(default="", title="雀魂账号ID")
    uuid: str = Field(default="", title="牌谱UUID")
    paipu_type: int = Field(default=-1, title="牌谱类型")
    paipu_type_name: str = Field(default="", title="牌谱类型名称")

    @classmethod
    @with_session
    async def insert_data(
        cls: Type[T_MajsPaipu],
        session: AsyncSession,
        uuid: str,
        account_id: str,
        paipu_type: int,
        paipu_type_name: str,
    ) -> int:
        return await cls.full_insert_data(
            uuid=uuid,
            account_id=account_id,
            paipu_type=paipu_type,
            paipu_type_name=paipu_type_name,
        )

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

    @classmethod
    @with_read_session
    async def get_group_uids(
        cls,
        session: AsyncSession,
        bot_id: str,
        real_bot_id: str,
        group_id: str,
    ) -> set[str]:
        """本群出现过的用户、或在本群绑定的用户，他们绑定的全部 UID"""
        # Bind.group_id 只记首次绑定的群，换个群就对不上，所以再按 CoreUser 的群成员找；
        # CoreUser.bot_id 存的是 Event.real_bot_id，不是 Event.bot_id
        members = select(col(CoreUser.user_id)).where(
            col(CoreUser.bot_id) == real_bot_id,
            col(CoreUser.group_id) == group_id,
        )
        stmt = select(col(cls.uid)).where(
            col(cls.bot_id) == bot_id,
            or_(col(cls.user_id).in_(members), col(cls.group_id) == group_id),
        )
        result = await session.execute(stmt)
        return {
            uid
            for joined in result.scalars().all()
            if joined
            for uid in joined.split("_")
            if uid
        }


class MajsUser(User, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")
    username: str = Field(default="", title="昵称")
    friend_code: str = Field(default="", title="好友码")

    lang: str = Field(default="zh", title="语言")
    login_type: int = Field(default=0, title="登录类型")

    account: str = Field(default="", title="账号")
    password: str = Field(default="", title="密码")
    token: str = Field(default="", title="Token For JP Account")

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

    @classmethod
    @with_session
    async def get_token(
        cls: Type["MajsUser"],
        session: AsyncSession,
        uid: str,
    ) -> Optional[str]:
        return await cls.get_user_attr_by_uid(uid, "token")


@site.register_admin
class MajsPaipuadmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="雀魂牌谱管理",
        icon="fa fa-braille",
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
        icon="fa fa-inbox",
    )  # type: ignore

    # 配置管理模型
    model = MajsUser


@site.register_admin
class MajsPushadmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="雀魂推送管理",
        icon="fa fa-paper-plane",
    )  # type: ignore

    # 配置管理模型
    model = MajsPush
