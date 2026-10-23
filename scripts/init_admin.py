"""Create the first admin while the backend is stopped; never log passwords."""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.orchestrator.config import Settings
from packages.adapters.platform_store import Platform
from packages.adapters.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username")
    parser.add_argument("--display-name", default="平台管理员")
    args = parser.parse_args()
    config = Settings.from_env()
    db = Store(config.state_dir)
    try:
        platform = Platform(db, config.catalog_root)
        if platform.initialized():
            raise ValueError("初始管理员已存在，请登录管理页面操作。")
        username = args.username or input("管理员用户名（字母、数字）：").strip()
        password = getpass.getpass("管理员密码（至少 10 位）：")
        if password != getpass.getpass("再次输入密码："):
            raise ValueError("两次密码不一致。")
        user = platform.initialize_admin(username, args.display_name, password)
        print("管理员已创建：" + user["username"] + "；历史数据已迁移，随后可启动服务。")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("初始化失败：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
