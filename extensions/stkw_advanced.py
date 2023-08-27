"""
SuperTuxKart Wrapper Advanced

This extension has an additions required for sessioned servers,
scheduled servers, tracked servers,
config manipulation etc.
Especially designed for STK-supertournament core functionality
on starting servers
"""


from admin_console import AdminCommandExtension, AdminCommandExecutor, paginate_range
from aiohndchain import AIOHandlerChain
from typing import Optional, MutableMapping, Union
from defusedxml import ElementTree as dElementTree
from xml.etree import ElementTree
from collections import defaultdict
from functools import partial
from configparser import ConfigParser
import emoji
import weakref
import os
import sys
import asyncio
import re
import logging
import datetime


joinmsg_parser = re.compile(r'New player (?P<username>\S+) with online id (?P<online_id>\d+) from '
                            r'(?P<ipv4_addr>[\d.]+)?(?P<ipv6_addr>[0-9a-fA-F:]+)?(?::(?P<port>\d+))? with '
                            r'(?P<version>.*)\..*')
validatemsg_parser = re.compile(r'(?P<username>\S+) validated')
leavemsg_parser = re.compile(r'(?P<username>\S+) disconnected')
modediff_parser = re.compile(r'Updating server info with new difficulty: (?P<difficulty>\d+), game mode: (?P<mode>\d+) to stk-addons\.')
modediff_obj = joinleave_logobject = gamestopped_obj = gameresumed_obj = 'ServerLobby'
modediff_level = joinleave_loglevel = gamestopped_lvl = gameresumed_lvl = logging.INFO
soccergoal_red = re.compile(r'(own_)?goal (\S*) red\.?')
soccergoal_blue = re.compile(r'(own_)?goal (\S*) blue\.?')
gamestopped_l = 'The game is stopped.'
gameresumed_l = 'The game is resumed.'
soccergoal_logobject = 'GoalLog'
soccergoal_loglevel = logging.INFO
main = sys.modules['__main__']
STKServer = main.STKServer
gamemode_names = (
    'normal grand prix',
    'time-trial grand prix',
    'follow the leader',  # unable for multiplayer
    'normal race',
    'time-trial',
    'easter egg hunt',  # unable for multiplayer
    'soccer',
    'free-for-all',
    'capture the flag'
)
difficulty_names = (
    'novice',
    'intermediate',
    'expert',
    'supertux'
)
defaultconf = {
    'RegularEnhancers': {
        'servers': ''
    },
    'SoccerEnhancers': {
        'servers': '',
        'sayNiceWhen69': True,
        'sayBrDeFlagsWhen17': True
    }
}


def load_stkdefault(ext: AdminCommandExtension):
    ext.logmsg(f'Loading "{ext.stkdefaultxml_path}"')
    with open(ext.stkdefaultxml_path, 'r') as file:
        ext.stkdefault = dElementTree.parse(file)
    ext.logmsg(f'"{ext.stkdefaultxml_path}" loaded')


def _startswith_predicate(name: str, stkservername: str):
    return stkservername.startswith(name)


async def stkserver_tab(cmd: AdminCommandExecutor, name: str = '', *args, argl: str):
    if argl:
        return list(cmd.servers.keys())
    elif args:
        return
    return list(filter(partial(_startswith_predicate, name), cmd.servers.keys()))


async def extension_init(ext: AdminCommandExtension):
    ext.stkdefaultxml_path = os.path.join(ext.ace.extpath, 'stkdefault.xml')
    confpath = ext.confpath = os.path.join(ext.ace.extpath, 'server_enhancers.conf')
    config = ext.config = ConfigParser(allow_no_value=True)
    load_stkdefault(ext)

    def _finalizer(name) -> bool:
        del ext.server_enhancers[name]
        return True

    def load_config():
        config.read_dict(defaultconf)
        if os.path.isfile(confpath):
            config.read(confpath)
        else:
            with open(confpath, 'x') as conffile:
                config.write(conffile)

    def save_config():
        with open(confpath, 'w' if os.path.isfile(confpath) else 'x') as conffile:
            config.write(conffile)
    ext.load_config = load_config
    ext.save_config = save_config
    load_config()

    class ServerEnhancer:
        _gamestart_parser = re.compile(r'Max ping from peers: \d+, jitter tolerance: \d+')
        _gamestart_obj = 'ServerLobby'
        _gamestartend_lvl = logging.INFO
        _gameend_parser = re.compile(r'A \d+GameProtocol protocol has been terminated.')
        _gameend_obj = 'ProtocolManager'

        def __init__(self, server: STKServer, expiration_mins: Optional[float] = None, expiry_deletefrom: Optional[MutableMapping[str, STKServer]] = None, *args, **kwds):
            super().__init__(*args, **kwds)
            self.server = weakref.proxy(server)
            self.name = server.name
            self._finalizer = partial(_finalizer, server.name)
            self.logger: logging.Logger = server.logger
            self.player_join = AIOHandlerChain(cancellable=True)
            self.player_leave = AIOHandlerChain(cancellable=False)
            self.game_start = AIOHandlerChain(cancellable=False)
            self.game_end = AIOHandlerChain(cancellable=False)
            self.game_stop = AIOHandlerChain(cancellable=False)
            self.game_resume = AIOHandlerChain(cancellable=False)
            self.game_stopped = False  # only useful for supertournament servers
            self.game_running = False  # indicates whether or not the game is happening on the server
            self.players = set()
            self.valid_players = set()
            if not server.empty_server.is_set():
                self.logger.warning(f'Enhancer [{server.name}] is initialized with non-empty server. Player list is not synchronized')
            server.log_event.add_handler(self.handle_stdout)
            self.expiry_timer: Optional[asyncio.Task] = None
            self.saveonempty_task: Optional[asyncio.Task] = None
            self.expiration_seconds: Optional[float] = None
            self.expiry_deletefrom: Optional[MutableMapping[str, STKServer]] = None
            self.cfgpath = os.path.join(server.cwd, server.cfgpath)
            self.servercfg: ElementTree.Element
            self.load_serverconfig()
            self.gamemode = int(self.servercfg.find('server-mode').attrib.get('value', '3'))
            self.difficulty = int(self.servercfg.find('server-difficulty').attrib.get('value', '3'))

        def __del__(self):
            try:
                _le: AIOHandlerChain = self.server.log_event
                _le.remove_handler(self.handle_stdout)
            except (ReferenceError, KeyError, ValueError):
                pass
            for task in (self.expiry_timer, self.saveonempty_task):
                if task is not None:
                    if not task.done():
                        task.cancel()
            self.logger.info(f"Enhancer [{self.name}] has been finished.")

        def cleanup(self):
            self.server.log_event.remove_handler(self.handle_stdout)
            for task in (self.expiry_timer, self.saveonempty_task):
                if task is not None:
                    if not task.done():
                        task.cancel()

        def load_serverconfig(self):
            try:
                self.logger.info(f"Enhancer [{self.server.name}] loading server config \"{self.cfgpath}\"")
                if os.path.isfile(self.cfgpath):
                    try:
                        with open(self.cfgpath, 'r') as file:
                            self.servercfg = dElementTree.fromstring(file.read())
                    except ElementTree.ParseError:
                        self.logger.exception(f"Enhancer [{self.server.name}] failed to parse server config")
                    self.logger.info(f"Enhancer [{self.server.name}] server config loaded")
                else:
                    with open(ext.stkdefaultxml_path, 'r') as file:
                        self.servercfg = dElementTree.fromstring(file)
                    if not self.server.active:
                        self.save_serverconfig()
            except Exception:
                self.logger.exception('load_serverconfig')
                raise

        def save_serverconfig(self, later=False):
            self.logger.info(f"Enhancer [{self.server.name}] saving server config")
            # make sure server is not running
            if self.server.active and not later:
                raise RuntimeError('server is running, cannot modify config')
            elif later and self.saveonempty_task is not None:
                if not self.saveonempty_task.done():
                    return
            if later:
                _tsk = asyncio.create_task(self._save_on_empty())
                self.saveonempty_task = _tsk
                ext.tasks[_tsk.get_name()] = _tsk
                return
            _root = ElementTree.ElementTree(self.servercfg)
            with open(self.cfgpath, 'wb' if os.path.isfile(self.cfgpath) else 'xb') as file:
                _root.write(file)

        async def _save_on_empty(self):
            await self.server.empty_server.wait()
            self.server.restart = False
            await self.server.stop()
            if self.server.active:
                self.logger.info(f'_save_on_empty({self.server.name}) server is active')
                await asyncio.sleep(0)
            self.save_serverconfig()
            self.logger.info(f'Config modified for {self.server.name}')
            await self.server.launch()

        async def kick(self, username: str, noblock=False):
            await self.chat(f"/kick {username}", noblock=noblock, allow_cmd=True)

        async def stuff_noblock(self, cmdline: str):
            _b = cmdline.encode() + b'\n'
            _process: asyncio.subprocess.Process = self.server.process
            _process.stdin.write(_b)
            await _process.stdin.drain()

        async def chat(self, message: str, noblock=False, allow_cmd=False):
            """Execute chat command. Prevents from executing lobby commands, if allow_cmd is False (default)
            Use noblock=True if this command executed in a log handler"""
            _data = f"chat {' ' if message.startswith('/') and not allow_cmd else ''}{message}"
            if noblock:
                _process: asyncio.subprocess.Process = self.server.process
                _process.stdin.write(_data.encode() + b'\n')
                await _process.stdin.drain()
            else:
                await self.server.stuff(_data)

        async def handle_stdout(self, event: AIOHandlerChain, message: str, *args, level: int, objectname: str, **kwargs):
            if objectname == joinleave_logobject and level == joinleave_loglevel:
                _joinmatch = joinmsg_parser.fullmatch(message)
                _validatematch = validatemsg_parser.fullmatch(message)
                _leavematch = leavemsg_parser.fullmatch(message)
                if _joinmatch:
                    username = _joinmatch.group('username')
                    if username not in self.players:
                        if await self.player_join.emit(username, _match=_joinmatch):
                            self.players.add(username)
                        else:
                            await self.kick(username, True)
                elif _validatematch:
                    username = _validatematch.group('username')
                    self.valid_players.add(username)
                elif _leavematch:
                    username = _leavematch.group('username')
                    if username in self.players:
                        if await self.player_leave.emit(username, _match=_leavematch):
                            try:
                                self.players.remove(username)
                            except ValueError:
                                pass
                            try:
                                self.valid_players.remove(username)
                            except ValueError:
                                pass
            if objectname == self._gamestart_obj and level == self._gamestartend_lvl:
                _match = self._gamestart_parser.fullmatch(message)
                if _match and not self.game_running:
                    await self.game_start.emit(self)
                    self.game_stopped = False
                    self.game_running = True
            if objectname == self._gameend_obj and level == self._gamestartend_lvl:
                _match = self._gameend_parser.fullmatch(message)
                if _match:
                    await self.game_end.emit(self)
                    self.game_stopped = False
                    self.game_running = False
            if objectname == modediff_obj and level == modediff_level:
                _modediff_match = modediff_parser.fullmatch(message)
                if _modediff_match:
                    self.gamemode = int(_modediff_match.group('mode'))
                    self.difficulty = int(_modediff_match.group('difficulty'))
            if objectname == gamestopped_obj and level == gamestopped_lvl and message == gamestopped_l:
                await self.game_stop.emit(self)
                self.game_stopped = True
            if objectname == gameresumed_obj and level == gameresumed_lvl and message == gameresumed_l:
                await self.game_resume.emit(self)
                self.game_stopped = False

        async def _expiry_timer(self):
            await asyncio.sleep(self.expiration_seconds)
            self.logger.info(f'[{self.server.name}] Server expired. Shutting down...')
            self.server.restart = False
            await self.server.stop()
            if self.expiry_deletefrom is not None:
                del self.expiry_deletefrom[self.server.name]

        def expire_at(self, at: datetime.datetime, utc=False):
            if utc:
                _now = datetime.datetime.utcnow()
            else:
                _now = datetime.datetime.now()
            _td = (at - _now)
            _seconds = _td.total_seconds()
            self.logger.info(f'[{self.server.name}] Server expires at {at.ctime()} or {_seconds / 60} minutes')
            self.expiration_seconds = _seconds
            if self.expiry_timer is not None:
                if not self.expiry_timer.done():
                    self.expiry_timer.cancel()
            self.expiry_timer = asyncio.create_task(self._expiry_timer())
            ext.tasks[self.expiry_timer.get_name()] = self.expiry_timer

        def expire_in(self, seconds: float):
            self.expiration_seconds = seconds
            self.logger.info(f'[{self.server.name}] Server expires in {seconds / 60} minutes')
            if self.expiry_timer is not None:
                if not self.expiry_timer.done():
                    self.expiry_timer.cancel()
            self.expiry_timer = asyncio.create_task(self._expiry_timer())
            ext.tasks[self.expiry_timer.get_name()] = self.expiry_timer
    ext.ServerEnhancer = ServerEnhancer

    class STKSoccer(ServerEnhancer):
        def __init__(self, server: STKServer, no_nice=False, no_brde=False, *args, **kwds):
            super().__init__(*args, server, **kwds)
            # event argument is player name
            self.goal = AIOHandlerChain(cancellable=False)
            self.resetScore()
            self.game_start.add_handler(self.resetScore)
            # im too young to ####### so pls dont say anything about 69
            self.no_nice = no_nice
            # i for some reason hate [message delete by moderator]
            self.no_brde = no_brde

        def cleanup(self):
            super().cleanup()
            self.game_start.remove_handler(self.resetScore)

        def resetScore(self, *args, **kwargs):
            # don't forget to reset it when necessary
            self.score_red = 0
            self.score_blue = 0

        async def handle_stdout(self, event: AIOHandlerChain, message: str, *args, level: int, objectname: str, **kwargs):
            await super().handle_stdout(event, message, objectname=objectname, level=level)
            if objectname == soccergoal_logobject and level == soccergoal_loglevel and not self.game_stopped:
                _match_red = soccergoal_red.fullmatch(message)
                _match_blue = soccergoal_blue.fullmatch(message)
                if _match_red:
                    if await self.goal.emit(_match_red.group(2), blue=False, own=bool(_match_red.group(1))):
                        self.score_red += 1
                if _match_blue:
                    if await self.goal.emit(_match_blue.group(2), blue=True, own=bool(_match_blue.group(1))):
                        self.score_blue += 1
                if not self.no_nice:
                    if self.score_red == 6 and self.score_blue == 9:
                        self.logger.info(f'Enhancer [{self.server.name}] 6-9 nice!')
                        await self.chat('nice', noblock=True)
                if not self.no_brde:
                    if (self.score_red == 1 and self.score_blue == 7) or (self.score_red == 7 and self.score_blue == 1):
                        self.logger.info(f'Enhancer [{self.server.name}] {self.score_red}-{self.score_blue} brazil and germany be like:')
                        await self.chat(emoji.emojize(':Brazil: :Germany:'), noblock=True)
    ext.STKSoccer = STKSoccer

    def enhance_server(name: str, cls=ServerEnhancer) -> ServerEnhancer:
        return cls(ext.ace.servers[name])

    ext.server_enhancers: Union[defaultdict, MutableMapping[str, ServerEnhancer]] = defaultdict(enhance_server)

    async def stk_enhance(cmd: AdminCommandExecutor, name: str, class_=ServerEnhancer):
        if name not in ext.ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        if name in ext.server_enhancers:
            cmd.error('Server already enhanced', log=False)
            return
        ext.server_enhancers[name] = class_(ext.ace.servers[name])
        cmd.print('Server enhanced')
    ext.add_command(partial(stk_enhance, class_=ServerEnhancer), 'stk-enhance', ((str, 'name'), ),
                    description='Registers the server in the enhancer to handle stuff',
                    atabcomplete=stkserver_tab)
    ext.add_command(partial(stk_enhance, class_=STKSoccer), 'stk-ensoccer', ((str, 'name'), ),
                    description='Registers the soccer server in the enhancer to track goals',
                    atabcomplete=stkserver_tab)

    async def stk_enhancers(cmd: AdminCommandExecutor, cpage=1):
        _len = len(ext.server_enhancers)
        _maxpage, _start, _end = paginate_range(_len, 10, cpage)
        cmd.print(f'Enhancers (page {cpage} of {_maxpage}):')
        _enhancers = tuple(ext.server_enhancers.values())
        cmd.print(*(f'#{i}. Server "{enhancer.server.name}", python class "{type(enhancer).__name__}"' for i, enhancer in ((i, _enhancers[i]) for i in range(_start, _end))), sep='\n')
    ext.add_command(stk_enhancers, 'stk-enhancers', optargs=((int, 'page'), ), description='Shows the list of enhancers')

    async def stk_unenhance(cmd: AdminCommandExecutor, name: str, force=False):
        if name not in ext.server_enhancers:
            cmd.error('Server is not enhanced', log=False)
            return
        _enhancer: ServerEnhancer = ext.server_enhancers[name]
        if _enhancer.saveonempty_task is not None and not force:
            if not _enhancer.saveonempty_task.done():
                cmd.error('This server has pending save task, which means that it has unsaved changes in the config.\n'
                          'To forcefully unenhance the server, specify "yes" as the second argument.', log=False)
                return
        _enhancer.cleanup()
        del ext.server_enhancers[name]
        del _enhancer
        cmd.print('Server unenhanced')
    ext.add_command(stk_unenhance, 'stk-unenhance', ((str, 'name'), ), ((bool, 'force'), ),
                    'Delete an enhancer for STK server. Still may leave a trace.',
                    atabcomplete=stkserver_tab)

    async def stk_soccerscore(cmd: AdminCommandExecutor, name: str):
        if name not in ext.server_enhancers:
            cmd.error(f'Server doesn\'t exist or not enhanced. To enhance this server, do stk-ensoccer {name}', log=False)
            return
        _enhancer: STKSoccer = ext.server_enhancers[name]
        if not isinstance(_enhancer, STKSoccer):
            cmd.error(f'This enhancer is not a soccer enhancer. Re-enhance this server with stk-ensoccer {name}', log=False)
            return
        cmd.print(f'Score: {_enhancer.score_red} - {_enhancer.score_blue}{" (nice)" if _enhancer.score_red == 6 and _enhancer.score_blue == 9 else ""}')
    ext.add_command(stk_soccerscore, 'stk-score', ((str, 'name'), ),
                    description='View the score of the soccer server',
                    atabcomplete=stkserver_tab)

    async def stk_modediff(cmd: AdminCommandExecutor, name: str):
        if name not in ext.server_enhancers:
            cmd.error(f'Server doesn\'t exist or not enhanced. To enhance this server, do stk-enhance {name}', log=False)
            return
        _enhancer: ServerEnhancer = ext.server_enhancers[name]
        _mode = gamemode_names[_enhancer.gamemode]
        _difficulty = difficulty_names[_enhancer.difficulty]
        cmd.print(f'Server mode: {_mode}, difficulty: {_difficulty}')
    ext.add_command(stk_modediff, 'stk-modediff', ((str, 'name'), ),
                    description='Get the last known mode and difficulty for a server',
                    atabcomplete=stkserver_tab)

    async def stk_69(cmd: AdminCommandExecutor, name: str, state: Optional[bool] = None):
        if name not in ext.server_enhancers:
            cmd.error(f'Server doesn\'t exist or not enhanced. To enhance this server, do stk-enhance {name}', log=False)
            return
        _enhancer: STKSoccer = ext.server_enhancers[name]
        if not isinstance(_enhancer, STKSoccer):
            cmd.error(f'This enhancer is not a soccer enhancer. Re-enhance this server with stk-ensoccer {name}', log=False)
            return
        if state is not None:
            _enhancer.no_nice = not state
        cmd.print(f'6-9 reference is {"disabled" if _enhancer.no_nice else "enabled"}')
    ext.add_command(stk_69, 'stk-69', ((str, 'name'), ), ((bool, 'state'), ),
                    description='Get, enable or disable the sex pose number reference of saying "nice"'
                                ' as the server when score reaches 6 - 9',
                    atabcomplete=stkserver_tab)

    ext.logmsg('Autoenhancing the servers...')
    for servername in config.get('RegularEnhancers', 'servers', fallback='').split(' '):
        if servername in ext.server_enhancers or not servername:
            continue
        ext.server_enhancers[servername] = ServerEnhancer(ext.ace.servers[servername])
        ext.logmsg(f'{servername} enhanced as a regular server')
    no_nice = not config.getboolean('SoccerEnhancers', 'sayNiceWhen69', fallback=True)
    for servername in config.get('SoccerEnhancers', 'servers', fallback='').split(' '):
        if servername in ext.server_enhancers or not servername:
            continue
        ext.server_enhancers[servername] = STKSoccer(ext.ace.servers[servername], no_nice=no_nice)
        ext.logmsg(f'{servername} enhanced as a soccer server')


async def extension_cleanup(ext: AdminCommandExtension):
    for enhancer in ext.server_enhancers.values():
        enhancer.cleanup()
