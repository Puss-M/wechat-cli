"""wechat-cli 入口"""

import click

from .core.context import AppContext

_VERSION = "1.0.0"


class _LazyAppContext:
    """Create the data context only when a command first needs it."""

    def __init__(self, config_path):
        self._config_path = config_path
        self._app = None

    def _get_app(self):
        if self._app is None:
            try:
                self._app = AppContext(self._config_path)
            except FileNotFoundError as e:
                click.echo(str(e), err=True)
                raise click.exceptions.Exit(1) from e
            except Exception as e:
                click.echo(f"初始化失败: {e}", err=True)
                raise click.exceptions.Exit(1) from e
        return self._app

    def __getattr__(self, name):
        return getattr(self._get_app(), name)


@click.group()
@click.version_option(version=_VERSION, prog_name="wechat-cli")
@click.option("--config", "config_path", default=None, envvar="WECHAT_CLI_CONFIG",
              help="config.json 路径（默认自动查找）")
@click.pass_context
def cli(ctx, config_path):
    """WeChat CLI — 查询微信消息、联系人等数据

    \b
    使用示例:
      wechat-cli init                                # 首次使用：提取密钥
      wechat-cli sessions                            # 最近会话列表
      wechat-cli sessions --limit 10                 # 最近 10 个会话
      wechat-cli history "张三" --limit 20          # 查看张三的最近 20 条消息
      wechat-cli history "AI交流群" --start-time "2026-04-01"  # 指定时间范围
      wechat-cli search "Claude" --chat "AI交流群"   # 在指定群里搜索关键词
      wechat-cli search "你好" --limit 50           # 全局搜索
      wechat-cli contacts --query "李"              # 搜索联系人
      wechat-cli new-messages                       # 获取增量新消息
    """
    # init/version and help do not need an initialized AppContext.
    if ctx.invoked_subcommand in ("init", "version"):
        return
    ctx.obj = _LazyAppContext(config_path)


# 注册子命令
from .commands.contacts import contacts
from .commands.export import export
from .commands.favorites import favorites
from .commands.history import history
from .commands.init import init
from .commands.members import members
from .commands.moments import moments
from .commands.moments_ui import moments_ui
from .commands.new_messages import new_messages
from .commands.search import search
from .commands.sessions import sessions
from .commands.stats import stats
from .commands.unread import unread

cli.add_command(init)
cli.add_command(sessions)
cli.add_command(history)
cli.add_command(search)
cli.add_command(contacts)
cli.add_command(new_messages)
cli.add_command(members)
cli.add_command(export)
cli.add_command(stats)
cli.add_command(unread)
cli.add_command(favorites)
cli.add_command(moments)
cli.add_command(moments_ui)


if __name__ == "__main__":
    cli()
