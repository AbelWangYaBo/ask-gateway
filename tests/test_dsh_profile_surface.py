"""DSH profile 工具面契约：离线夹具断言 + 钉版本 runtime 实构建断言。

两层防线：
- 静态层：仓库内 fixtures/ask-gateway.dump.yml 必须持续满足裁剪口径，
  即使本机没有 runtime 二进制（轻量 CI venv）也能跑。
- 实机构层：用钉版本 runtime 在临时 DSH_HOME 真跑一遍构建脚本，
  证明夹具与当前二进制行为一致、版本没有漂移。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = REPO_ROOT / "dsh_profile"
FIXTURE_DUMP = PROFILE_DIR / "fixtures" / "ask-gateway.dump.yml"
DENYLIST_PATCH = PROFILE_DIR / "tool-denylist.patch.yml"
BUILD_SCRIPT = PROFILE_DIR / "build_profile.py"


def _load_build_module() -> Any:
    spec = importlib.util.spec_from_file_location("dsh_build_profile", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bp = _load_build_module()


def test_denylist_patch_covers_every_denied_id() -> None:
    """patch 文件与脚本里的 DENIED_TOOL_IDS 必须同源，漏写即失败。"""
    patch_text = DENYLIST_PATCH.read_text(encoding="utf-8")
    for tool_id in bp.DENIED_TOOL_IDS:
        assert f"id: {tool_id}" in patch_text, f"{tool_id} 未写入裁剪 patch"


def test_committed_fixture_satisfies_surface_contract() -> None:
    entries = bp._load_yaml_with_js_tags(FIXTURE_DUMP.read_text(encoding="utf-8"))
    bp.validate_surface(entries)


def test_fixture_has_no_machine_local_paths() -> None:
    """夹具是跨机器契约，不得携带捕获机的绝对路径（见 dsh_profile/README 捕获步骤）。"""
    text = FIXTURE_DUMP.read_text(encoding="utf-8")
    for marker in ("/Volumes/", "/Users/", "/home/", "/tmp/", "dsh-build-test"):
        assert marker not in text, f"夹具混入本机路径 {marker}"


def test_validate_surface_rejects_enabled_denied_tool() -> None:
    entries = [
        {"id": tool_id, "disabled": tool_id != "tool-bash"}
        for tool_id in bp.DENIED_TOOL_IDS
    ]
    entries.extend(
        [
            {"id": "tool-web", "disabled": False},
            {"id": "web-search-deepseek", "disabled": False},
        ]
    )
    with pytest.raises(bp.ProfileBuildError, match="tool-bash 未被禁用"):
        bp.validate_surface(entries)


def test_validate_surface_rejects_missing_web_search() -> None:
    entries = [{"id": tool_id, "disabled": True} for tool_id in bp.DENIED_TOOL_IDS]
    with pytest.raises(bp.ProfileBuildError, match="tool-web"):
        bp.validate_surface(entries)


def test_validate_surface_rejects_renamed_upstream_plugin() -> None:
    entries = [
        {"id": tool_id, "disabled": True}
        for tool_id in bp.DENIED_TOOL_IDS
        if tool_id != "tool-fs"
    ]
    entries.extend(
        [
            {"id": "tool-web", "disabled": False},
            {"id": "web-search-deepseek", "disabled": False},
        ]
    )
    with pytest.raises(bp.ProfileBuildError, match="tool-fs"):
        bp.validate_surface(entries)


@pytest.fixture
def runtime_binary() -> Path:
    pytest.importorskip("deepseek_harness_runtime")
    from deepseek_harness_runtime import resolve_bundled_launch_args

    return Path(resolve_bundled_launch_args()[0])


def test_runtime_version_is_pinned(runtime_binary: Path) -> None:
    bp.assert_runtime_version(runtime_binary)


def test_build_profile_end_to_end_in_tmp_home(
    runtime_binary: Path, tmp_path: Path
) -> None:
    home = tmp_path / "dsh-home"
    patch_text = DENYLIST_PATCH.read_text(encoding="utf-8")

    entries = bp.provision_home(runtime_binary, home, patch_text)
    bp.validate_surface(entries)

    # 幂等：再跑一次仍然通过，且 profile 目录结构不变
    entries_again = bp.provision_home(runtime_binary, home, patch_text)
    bp.validate_surface(entries_again)

    profile_dir = home / "profiles" / bp.PROFILE_NAME
    assert (profile_dir / "cordis.patch.yml").read_text(encoding="utf-8") == patch_text
    assert (profile_dir / "cordis.yml").is_file()
    assert (profile_dir / "package.json").is_file()

    # 实机构建结果与提交夹具的工具面一致（按 id 比对 disabled 口径）
    fixture_entries = bp._load_yaml_with_js_tags(FIXTURE_DUMP.read_text(encoding="utf-8"))
    live_by_id = {e["id"]: e for e in entries if isinstance(e, dict) and "id" in e}
    fixture_by_id = {e["id"]: e for e in fixture_entries if isinstance(e, dict) and "id" in e}
    for tool_id in (*bp.DENIED_TOOL_IDS, *bp.REQUIRED_ENABLED_IDS):
        assert live_by_id[tool_id].get("disabled") is fixture_by_id[tool_id].get(
            "disabled"
        )
