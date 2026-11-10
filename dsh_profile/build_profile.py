"""DSH ask-gateway profile 构建：一次性人工脚本，幂等，可反复执行。

用法（在项目根目录）：
    # 默认按 UPSTREAM_KEYS 派生的全部 label home 构建（data/dsh/homes/<label>）
    uv run python dsh_profile/build_profile.py
    # 显式指定一个或多个 DSH_HOME（无 UPSTREAM_KEYS 的环境/冒烟测试）
    uv run python dsh_profile/build_profile.py --dsh-home /path/to/dsh-home

设计约束：
- 绝不放进应用 lifespan；profile 是部署前置条件，由人工在目标环境执行，
  与 alembic 迁移同级。
- 核心 provision/校验逻辑在 ask_gateway.services.profile_provision，
  与运行时动态 Key 的懒初始化共用同一实现；本文件只是 CLI 壳。
- 每个 DSH_HOME 下重建 profiles/ask-gateway（从运行时随包 sdk 模板复制），
  再把同目录 tool-denylist.patch.yml 覆盖写入其 cordis.patch.yml。
  --from-default-profile 语义即“用模板全新替换自定义 profile”，
  重复执行得到同一结果。
- 运行时版本钉在 EXPECTED_RUNTIME_VERSION，与 uv.lock 的
  deepseek-harness-sdk==0.2.2rc1 对齐；漂移直接失败，不静默吃新版本。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ask_gateway.config import Settings, get_settings
from ask_gateway.logging_conf import configure_logging, get_logger
from ask_gateway.services.profile_provision import (
    DENIED_TOOL_IDS,
    EXPECTED_RUNTIME_VERSION,
    PROFILE_NAME,
    REQUIRED_ENABLED_IDS,
    ProfileBuildError,
    assert_runtime_version,
    patch_text_from_repo,
    provision_home,
    resolve_binary,
)
from ask_gateway.services.profile_provision import (
    _load_yaml_with_js_tags as _load_yaml_with_js_tags,
)
from ask_gateway.services.profile_provision import (
    validate_surface as validate_surface,
)

# re-export：tests/test_dsh_profile_surface.py 经 importlib 把本文件当断言来源
__all__ = [
    "DENIED_TOOL_IDS",
    "EXPECTED_RUNTIME_VERSION",
    "PROFILE_NAME",
    "REQUIRED_ENABLED_IDS",
    "ProfileBuildError",
    "_load_yaml_with_js_tags",
    "assert_runtime_version",
    "patch_text_from_repo",
    "provision_home",
    "resolve_binary",
    "validate_surface",
]

log = get_logger(component="profile-build")

_PATCH_FILE = Path(__file__).with_name("tool-denylist.patch.yml")


def _target_homes(settings: Settings, explicit: list[Path]) -> list[Path]:
    if explicit:
        return [path.expanduser().resolve() for path in explicit]
    from ask_gateway.db import session as db_session
    from ask_gateway.services import upstream_key_service

    session_factory = db_session.get_session_factory()
    with session_factory() as session:
        labels = upstream_key_service.active_pool_labels(session)
    if not labels and settings.upstream_keys:
        # DB 未迁移/未引导时的兜底：按 env 序号派生（与旧行为一致）
        labels = [f"upstream-{i + 1}" for i in range(len(settings.upstream_keys))]
    if not labels:
        raise ProfileBuildError(
            "数据库与 UPSTREAM_KEYS 均无上游 Key，且未传 --dsh-home，没有可构建的目标 home"
        )
    return [settings.dsh_home_root / label for label in labels]


def build(patch_text: str, homes: list[Path], binary: Path) -> None:
    assert_runtime_version(binary)
    for home in homes:
        entries = provision_home(binary, home, patch_text)
        log.info(
            "profile built",
            home=str(home),
            profile=PROFILE_NAME,
            plugins=len(entries),
            denied=len(DENIED_TOOL_IDS),
        )


def main() -> None:
    configure_logging(log_json=False)
    parser = argparse.ArgumentParser(description="构建 ask-gateway DSH profile")
    parser.add_argument(
        "--dsh-home",
        type=Path,
        action="append",
        default=[],
        help="目标 DSH_HOME，可重复；缺省按数据库/UPSTREAM_KEYS 派生全部 label home",
    )
    args = parser.parse_args()

    settings = get_settings()
    binary = resolve_binary(settings)
    homes = _target_homes(settings, args.dsh_home)
    patch_text = _PATCH_FILE.read_text(encoding="utf-8")
    build(patch_text, homes, binary)
    log.info("all profiles ready", homes=[str(h) for h in homes])


if __name__ == "__main__":
    main()
