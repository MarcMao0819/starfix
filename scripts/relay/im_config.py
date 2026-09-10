#!/usr/bin/env python3
"""IM 接力的配置来源：全部从环境变量 + 一个 JSON 文件读，仓里不留任何真实标识。

为什么单独一个模块：四个 relay 脚本原本各自写死了应用 ID、收件人表和密钥文件路径。
脱敏成占位之后它们"看着干净但跑不了"，把加载逻辑收在一处，四个脚本各改一行即可。

配置从哪来：
  IM_CONTACTS_JSON   收件人与应用配置的 JSON 路径
                     默认 $FLEET_HOME/im-contacts.json
                     格式见 examples/im-contacts.sample.json

失败时**报一句人话，不抛栈**——这类错几乎总是"没配"而不是"代码坏了"，
栈只会让人去读源码，而真正要做的是去建那个文件。
"""
import json
import os
import sys


class ConfigError(Exception):
    """配置缺失/不合法。调用方应打印 str(e) 后退出，不要打印堆栈。"""


def fleet_home():
    home = os.environ.get('FLEET_HOME')
    if not home:
        raise ConfigError(
            '缺少环境变量 FLEET_HOME。\n'
            '  它指向舰队工作目录（任务书、回执、激活器 json 都在它下面）。\n'
            '  例：export FLEET_HOME="$HOME/fleet-data"\n'
            '  全部环境变量见 scripts/README-env.md')
    return home


def contacts_path():
    p = os.environ.get('IM_CONTACTS_JSON')
    if p:
        return p
    return os.path.join(fleet_home(), 'im-contacts.json')


def load():
    """返回 dict：{app_id, app_secret, cc_id, contacts{名称:ID}, titles{名称:抬头}}"""
    path = contacts_path()
    if not os.path.exists(path):
        raise ConfigError(
            f'找不到 IM 配置文件：{path}\n'
            '  这个文件存放 IM 应用 ID、密钥来源和收件人表，**不进仓库**。\n'
            '  照 examples/im-contacts.sample.json 建一份，或用 IM_CONTACTS_JSON 指到别处。')
    try:
        with open(path, encoding='utf-8') as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f'IM 配置文件不是合法 JSON：{path}\n  {e}')

    app_id = cfg.get('app_id') or os.environ.get('IM_APP_ID')
    if not app_id:
        raise ConfigError(f'{path} 里缺 app_id（也可用环境变量 IM_APP_ID 提供）')

    # 密钥优先级：环境变量 > 密钥文件 > 配置里的明文（明文只为兼容，强烈不建议）
    secret = os.environ.get('IM_APP_SECRET')
    if not secret and cfg.get('app_secret_file'):
        sf = os.path.expandvars(os.path.expanduser(cfg['app_secret_file']))
        if '${' in sf:
            # 展开后仍带 ${...} 说明那个环境变量没设——直接说清是哪个，
            # 别让人对着一个看不懂的路径猜
            missing = sf[sf.index('${') + 2: sf.index('}')] if '}' in sf else '?'
            raise ConfigError(
                f'app_secret_file 里的环境变量 {missing} 没有设置，路径展不开：{sf}\n'
                f'  先 export {missing}=... ，或把 app_secret_file 改成绝对路径。')
        if not os.path.exists(sf):
            raise ConfigError(f'app_secret_file 指向的文件不存在：{sf}')
        secret = open(sf, encoding='utf-8').read().strip()
    if not secret:
        secret = cfg.get('app_secret')
    if not secret:
        raise ConfigError(
            f'拿不到 IM 应用密钥。三选一：\n'
            f'  ① 环境变量 IM_APP_SECRET（推荐）\n'
            f'  ② {path} 里的 app_secret_file 指向一个只含密钥的文件\n'
            f'  ③ {path} 里的 app_secret 明文（不建议：配置文件容易被误提交）')

    contacts = cfg.get('contacts') or {}
    if not contacts:
        raise ConfigError(f'{path} 里 contacts 为空，没有可发送的收件人')

    cc_key = cfg.get('cc')
    cc_id = contacts.get(cc_key) if cc_key else None
    if cc_key and not cc_id:
        raise ConfigError(f'{path} 里 cc="{cc_key}"，但 contacts 里没有这个名字')

    return {'app_id': app_id, 'app_secret': secret, 'cc_id': cc_id,
            'cc_name': cc_key, 'contacts': contacts, 'titles': cfg.get('titles') or {}}


def load_or_exit():
    """给命令行脚本用：配置有问题就打印人话并退出 2，不抛栈。"""
    try:
        return load()
    except ConfigError as e:
        print(f'配置错误：{e}', file=sys.stderr)
        sys.exit(2)
