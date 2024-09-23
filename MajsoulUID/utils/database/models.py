from typing import Optional

from sqlmodel import Field
from gsuid_core.utils.database.base_models import Bind
from gsuid_core.webconsole.mount_app import PageSchema, GsAdminModel, site


class MajsBind(Bind, table=True):
    uid: Optional[str] = Field(default=None, title="雀魂UID")


@site.register_admin
class MajsBindadmin(GsAdminModel):
    pk_name = "id"
    page_schema = PageSchema(
        label="雀魂绑定管理",
        icon="fa fa-users",
    )  # type: ignore

    # 配置管理模型
    model = MajsBind
