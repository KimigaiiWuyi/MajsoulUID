from typing import Optional

from sqlmodel import Field
from gsuid_core.utils.database.base_models import Bind, Push, User
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site


class MajsPush(Push, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")
    user_id: str = Field(default='', title="用户ID")
    push_id: str = Field(default='off', title="是否开启推送")


class MajsBind(Bind, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")


class MajsUser(User, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")
    friend_code: str = Field(default='', title="好友码")


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
