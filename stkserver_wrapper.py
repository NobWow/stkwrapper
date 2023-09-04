"""
* stkserver_wrapper.py - main file for starting servers
* License: GNU LGPL v2.1
* Author: DernisNW (a.k.a. NobWow)
"""
import asyncio
import logging
import traceback
import os
import re
import shlex
# import traceback
# from shutil import rmtree
# from zipfile import ZipFile
# from math import floor
# from defusedxml import ElementTree as dElementTree
from logging.handlers import TimedRotatingFileHandler
from admin_console import AdminCommandExecutor, AdminCommandExtension, basic_command_set, paginate_range
from admin_console.ainput import colors, ARILogHandler
from admin_console.ainput import ansi_escape as ansi_escape_
from aiohndchain import AIOHandlerChain
from enum import IntEnum
from packaging.version import parse as parseVersion
from functools import partial
from contextlib import asynccontextmanager
from typing import Sequence, MutableSequence, Optional, Mapping, MutableMapping, Callable, Any


ansi_escape = re.compile(r'(?:\x9B|\x1B\[)[0-?]*[ -\/]*[@-~]')
_cfgfile_path = 'config.json'
debug_l = 'debug'
verbose_l = 'verbose'
info_l = 'info'
warn_l = 'warn'
error_l = 'error'
fatal_l = 'fatal'
server_attribs = ('cfgpath', 'datapath', 'executable_path', 'cwd',
                  'autostart', 'autorestart', 'timed_autorestart',
                  'timed_autorestart_interval', 'startup_timeout', 'shutdown_timeout',
                  'extra_env', 'extra_args')
no_yes = ('no', 'yes')
yes_match = re.compile(r' *[yY+1][yYeEaAhHpP ]*')
no_match = re.compile(r' *[nN\-0][nNoOpPeE ]*')
splitter = re.compile(r'[, ] *')
make_server_skipmsg = ("""Current working directory: "{cwd}" for relative path reference\n"""
                       """Press return to skip and set the default value "{default}" """)
make_server_msg = (
    # name
    """Enter the server name. It will be used for further interaction with the server,\nYou cannot change it later\n"""
    """but every name should be unique.""",
    # cfgpath
    """Enter the path to configuration file. It must have the XML format (.xml).\nYou can change it later\n{skipmsg}""",
    # datapath
    """Enter the path to the "data" directory that will be used for the new server.\nYou can change it later\n{skipmsg}\n"""
    """TIP: it is usually either /usr/share/supertuxkart\n    or in case of GIT version /path/to/stk-code""",
    # exec
    """Enter the path to supertuxkart executable file (program).\nYou can change it later\n{skipmsg}""",
    # cwd
    """Enter the path to server's working directory.\nYou can change it later\n{skipmsg}""",
    # autostart
    """Should server automatically start after wrapper has been launched? Hit y for yes or n for no.\nYou can change it later""",
    # autorestart
    """In case the server crashes, does it require automatic restart? Hit y for yes or n for no.\nYou can change it later""",
    # timed_autorestart + timed_autorestart_interval
    """Is it needed to restart the server every N minutes? Leave empty string or 0 if autorestarts aren't required.\n"""
    """You can change it later\n"""
    """Note: the server will not restart if there are players at the moment""",
    # startup_timeout
    """How many seconds the server has to initialize?"""
    """ When this timeout exceeds during server startup, the process is killed.\n{skipmsg}""",
    # shutdown_timeout
    """How many seconds the server has to shutdown?"""
    """When this timeout exceeds during server shutdown, the process is killed.\n{skipmsg}""",
    # extra_env
    """Advanced: which additional environment variables to pass to the process?\n"""
    """For example, you can specify XDG_DATA_HOME=/path/to/directory HOME=/some/directory/path\n"""
    """You can change it later\nTo clear extra argument, specify -""",
    # extra_args
    """Advanced: any additional arguments to the command line? Just leave it empty if you have no idea.\n"""
    """You can change it later\nTo clear extra argument, specify -"""
)


def load_config(ace: AdminCommandExecutor):
    ace.load_config()
    _ver = ace.config['stk_version'] = ace.config.get('stk_version', '1.3.0')
    ace.config['logpath'] = ace.config.get('logpath', 'logs')
    ace.config['servers'] = ace.config.get('servers', {})
    ace.config['datapath'] = ace.config.get('datapath', 'stk-code')
    ace.config['executable_path'] = ace.config.get('executable_path', 'supertuxkart')
    ace.config['autostart'] = ace.config.get('autostart', False)
    ace.config['autorestart'] = ace.config.get('autorestart', True)
    ace.config['autorestart_pause'] = ace.config.get('autorestart_pause', 10.0)
    ace.config['timed_autorestart'] = ace.config.get('timed_autorestart', False)
    ace.config['timed_autorestart_interval'] = ace.config.get('timed_autorestart_interval', False)
    ace.config['extra_env'] = ace.config.get('extra_env', None)
    ace.config['extra_args'] = ace.config.get('extra_args', [])  # json doesn't support immutable sequences, use mutable instead
    _global_logignores = ace.config['global_logignores'] = ace.config.get('global_logignores', {})
    ace.save_config()
    ace.global_logignores = make_logignores(_global_logignores)
    ace.stuff['stk_version'] = parseVersion(_ver)
    for servername, serverdata in ace.config['servers'].items():
        # cwd: str, autostart=False, autorestart=True, timed_autorestart=False,
        # timed_autorestart_interval: Optional[float] = None,
        # restarter_cond: Optional[asyncio.Condition] = None,
        # extra_args: Optional[Sequence[str]] = tuple()):
        if servername in ace.servers:
            server: STKServer = ace.servers[servername]
            for item in server_attribs:
                setattr(server, item, serverdata[item])
            local_logignore: dict = server.log_ignores.maps[0]
            assert type(local_logignore) is dict, 'because first mapping must be mutable'
            local_logignore.clear()
            local_logignore.update(make_logignores(serverdata.get('log_ignores', {})))
        else:
            ace.servers[servername] = STKServer(
                ace.logger, ace.ainput.writeln, servername, cfgpath=serverdata['cfgpath'],
                restarter_cond=ace.server_restart_cond, start_stop_guard=ace.start_stop_guard,
                datapath=serverdata.get('datapath', ace.config['datapath']),
                executable_path=serverdata.get('executable_path', ace.config['executable_path']),
                cwd=serverdata.get('cwd', ace.config.get('cwd', os.getcwd())),
                autostart=serverdata.get('autostart', ace.config['autostart']),
                autorestart=serverdata.get('autorestart', ace.config['autorestart']),
                autorestart_pause=serverdata.get('autorestart_pause', ace.config['autorestart_pause']),
                timed_autorestart=serverdata.get('timed_autorestart', ace.config['timed_autorestart']),
                timed_autorestart_interval=serverdata.get('timed_autorestart_interval', ace.config['timed_autorestart_interval']),
                extra_env=serverdata.get('extra_env', ace.config.get('extra_env', None)),
                extra_args=serverdata.get('extra_args', ace.config.get('extra_args', tuple())),
                global_logignores=ace.global_logignores,
                logignores=make_logignores(serverdata.get('log_ignores', {}))
            )


def make_logignores(logignores: Mapping[str, Mapping[str, Sequence[str]]]) -> MutableMapping[str, MutableMapping[int, MutableSequence[re.Pattern]]]:
    res = dict(
        (modname, dict((int(level), list(re.compile(pattern) for pattern in patterns)) for level, patterns in modignores.items()))
        for modname, modignores in logignores.items()
    )
    return res


async def _trigger_restart(ace) -> bool:
    try:
        async with ace.server_restart_cond:
            ace.server_restart_cond.notify_all()
        return True
    except RuntimeError:
        return False


def server_restart_clk(ace):
    """
    This is called every time when the server needs to be restarted
    But automatic server restart will only happen if no players are online at the moment
    """
    asyncio.create_task(_trigger_restart(ace))


class STKLogFilter(logging.Filter):
    def __init__(self, ace: AdminCommandExecutor, *args, **kwargs):
        self.ace = ace
        super().__init__(*args, **kwargs)

    def filter(record: logging.LogRecord):
        pass


class LogLevel(IntEnum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    FATAL = logging.FATAL


class STKServer:
    idle_command = '\x01'
    logstrip = re.compile(r'(?:\w+ +\w+ +\d+ +\d+:\d+:\d+ +\d+ )?\[(\w+) *\] +([^:]+)?: (.*)''\n?')
    ignore_idle = re.compile(f'Unknown command: {idle_command}')
    ready_loglevel = logging.INFO
    ready_objectname = 'ServerLobby'
    ready_pattern = re.compile(r'Server (\d+) is now online.')
    joinleave_objectname = 'STKHost'
    joinleave_pattern = re.compile(r'[a-f0-9.:]+ has just (?:dis)?connected. There are now (\d+) peers.')
    extra_leave_patterns = [
        ('STKHost', logging.INFO, re.compile(r'[a-f0-9.:]+ has not been validated for more than [0-9.]+ seconds, disconnect it by force.')),
        ('STKHost', logging.INFO, re.compile(r'[a-f0-9.:]+ \S+ with ping \d+ is higher than \d+ ms when not in game, kick.')),
        ('ServerLobby', logging.INFO, re.compile(r'\S+ banned by .+: \S+ (rowid: \d+, description: \S+).')),
    ]
    stop_command = b'quit\n'

    def __init__(self, logger: logging.Logger, writeln: Callable[[str], Any],
                 name: str, cfgpath: str, datapath: str, executable_path: str,
                 cwd: str, autostart=False, autorestart=True, autorestart_pause=10.0, timed_autorestart=False,
                 timed_autorestart_interval: Optional[float] = None,
                 startup_timeout: Optional[float] = None,
                 shutdown_timeout: Optional[float] = None,
                 restarter_cond: Optional[asyncio.Condition] = None,
                 extra_env: Optional[Mapping[str, str]] = None,
                 extra_args: Optional[Sequence[str]] = tuple(),
                 global_logignores: Optional[Mapping[str, Mapping[int, Sequence[re.Pattern]]]] = None,
                 logignores: Optional[MutableMapping[str, MutableMapping[int, MutableSequence[re.Pattern]]]] = None,
                 start_stop_guard: Optional[asyncio.Lock] = None):
        self.process: Optional[asyncio.subprocess.Process] = None
        if not os.path.isfile(executable_path):
            raise FileNotFoundError(f'supertuxkart executable "{executable_path}" not found', 'executable_path', executable_path)
        self.executable_path = executable_path
        if not os.path.isdir(cwd):
            raise FileNotFoundError(f'working directory "{cwd}" not found', 'cwd', cwd)
        self.cwd = cwd
        self.writeln = writeln
        self.active = False
        self.restart = False
        self.autostart = autostart
        self.autorestart = autorestart
        self.autorestart_pause = autorestart_pause
        self.timed_autorestart = timed_autorestart
        self.timed_autorestart_interval = timed_autorestart_interval
        self.startup_timeout = startup_timeout
        self.shutdown_timeout = shutdown_timeout
        self.empty_server = asyncio.Event()
        self.empty_server.set()
        self.name = name
        self.cfgpath = cfgpath
        if not os.path.isdir(datapath):
            raise FileNotFoundError(f'assets directory "{datapath}" not found', 'datapath', datapath)
        self.datapath = datapath
        self.logger = logger
        self.log_event = AIOHandlerChain()
        self.log_event.on_handler_error = self._loghandler_error
        self.ready_event = AIOHandlerChain(cancellable=False)
        self.restarter_task: Optional[asyncio.Task] = None
        self.restarter_cond = restarter_cond
        self.reader_task: Optional[asyncio.Task] = None
        self.errreader_task: Optional[asyncio.Task] = None
        self.timer_task: Optional[asyncio.Task] = None
        self.server_ready_task: Optional[asyncio.Task] = None
        self.show_stderr = False
        self.idle_cancellable = False
        self.lock = asyncio.Lock()
        # since 1.4, concurrent startup of servers is broken
        self.start_stop_guard = start_stop_guard
        if logignores is None:
            logignores = {}
        self.log_ignores: MutableMapping[str, MutableMapping[int, MutableSequence[re.Pattern]]] = logignores
        self.global_logignores = global_logignores
        # 'STKHost': {logging.WARNING: [re.compile(r'bad addon: asdasdasd')]}
        self.extra_args = extra_args
        self.extra_env = extra_env
        self.show_plain = False

    async def _loghandler_error(self, hndid: int, exc: Exception, *args, **kw):
        self.logger.exception(f"An exception is occurred when invoking handler #{hndid}:")

    def __del__(self):
        for task in (self.restarter_task, self.reader_task, self.errreader_task):
            if task is not None:
                if not task.done():
                    task.cancel()

    def add_logignore(self, modname: str, level: int, pattern: str):
        _pattern = re.compile(pattern)
        self.log_ignores[modname][level].append(_pattern)
        return _pattern

    def del_logignore(self, modname: str, level: int, id_: int):
        del self.log_ignores[modname][level][id_]

    async def launch(self):
        if self.process is not None:
            if self.process.returncode is not None:
                raise RuntimeError("the server is already running")
        # cmdline = (f"{shlex.quote(self.executable_path)} "
        #            f'--server-config={shlex.quote(self.cfgpath)} ' + ' '.join(
        #                shlex.quote(arg) for arg in self.extra_args
        #            ) + ' --network-console')
        _env = os.environ.copy()
        if self.extra_env is not None:
            # pass extra environment to the process
            _env.update(self.extra_env)
        _env['SUPERTUXKART_DATADIR'] = self.datapath
        # lock is released at ready call
        if self.start_stop_guard is not None:
            await self.start_stop_guard.acquire()
            self.server_ready_task = asyncio.create_task(self._waitready(self.startup_timeout))
        self.process = await asyncio.create_subprocess_exec(
            self.executable_path,
            f'--server-config={self.cfgpath}',
            *self.extra_args,
            '--network-console',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_env,
            cwd=self.cwd
        )
        self.restart = self.autorestart
        self.active = True
        self.reader_task = asyncio.create_task(self._reader(self.process.stdout))
        self.errreader_task = asyncio.create_task(self._error_reader(self.process.stderr))
        if self.timed_autorestart:
            self.timer_task = asyncio.create_task(self._timed_restarter())
        if self.restarter_cond is not None:
            self.restarter_task = asyncio.create_task(self._restarter())

    async def _waitready(self, timeout: Optional[float] = None):
        """
        Releases the start_stop_guard lock when the server becomes ready.
        If server doesn't become ready within a timeout, kills the server.
        """
        assert self.start_stop_guard is not None
        try:
            await asyncio.wait_for(self.ready_event.wait_for_successful(), timeout)
        except asyncio.TimeoutError:
            self.logger.warning(f'STK {self.name} has not become ready within {timeout} seconds, killing')
            self.process.kill()
        finally:
            self.start_stop_guard.release()

    async def stop(self, timeout: Optional[float] = None, from_timer=False, no_lock=False) -> bool:
        """
        Sends quit command to the server and waits until the process exit.
        On timeout kills the process
        If timeout is 0, kills the process without waiting for it
        Set self.restart = True to restart, False to stop
        """
        try:
            # sorry, can't use context manager here
            if self.start_stop_guard is not None:
                await self.start_stop_guard.acquire()
            if self.process is None:
                self.logger.debug('STKServer.stop: the server is not running')
                raise RuntimeError("the server is not running")
            elif self.process.returncode is not None:
                self.logger.debug('STKServer.stop: the server is already stopped')
                raise RuntimeError("the server is already stopped")
            if self.restarter_task is not None:
                self.restarter_task.cancel()
                self.restarter_task = None
            if self.timer_task is not None and not from_timer:
                self.timer_task.cancel()
                self.timer_task = None
            if timeout == 0:
                # Forcibly stop a server
                self.logger.warning(f'STK {self.name} was forcefully shut down.')
                self.process.kill()
                return
            elif timeout is None:
                timeout = self.shutdown_timeout
            if no_lock:
                self.logger.debug('STKServer.stop: no-lock stop command')
                self.process.stdin.write(self.stop_command)
                await self.process.stdin.drain()
            else:
                self.logger.debug('STKServer.stop: pre-interrupt')
                async with self.interrupting_idle_for():
                    self.logger.debug('STKServer.stop: interrupt')
                    self.process.stdin.write(self.stop_command)
                    await self.process.stdin.drain()
                    self.logger.debug('STKServer.stop: command sent')
                self.logger.debug('STKServer.stop: post-interrupt')
            if timeout is None:
                await self.process.wait()
                return True
            else:
                try:
                    await asyncio.wait_for(self.process.wait(), timeout)
                    return True
                except asyncio.TimeoutError:
                    self.logger.warning(f'STK {self.name} shutdown operation timed out, killing.')
                    self.process.kill()
                    return False
        except Exception:
            self.logger.exception('stop() failed:')
        finally:
            if self.start_stop_guard is not None:
                self.start_stop_guard.release()

    async def _error_reader(self, _stderr: asyncio.StreamReader):
        while not _stderr.at_eof():
            line = await _stderr.readline()
            if not self.show_stderr:
                continue
            if asyncio.iscoroutinefunction(self.handle_stderr):
                await self.handle_stderr(line.decode())
            else:
                self.handle_stderr(line.decode())

    async def _reader(self, _stdout: asyncio.StreamReader):
        self.logger.debug('_reader: start')
        while not _stdout.at_eof():
            try:
                async with self.lock:
                    pass
                self.idle_cancellable = True
                async with self.lock:
                    line = await _stdout.readline()
                    self.idle_cancellable = False
                    if asyncio.iscoroutinefunction(self.handle_stdout):
                        await self.handle_stdout(ansi_escape_.sub('', line.decode()))
                    else:
                        self.handle_stdout(ansi_escape_.sub('', line.decode()))
            except asyncio.CancelledError:
                if not self.active:
                    return
                else:
                    continue
            except Exception:
                self.logger.error(f'_reader: exception caught\n{traceback.format_exc()}')
        _returncode = self.process.returncode
        if _returncode is None:
            self.idle_cancellable = True
            await self.process.wait()
        _returncode = self.process.returncode
        self.logger.log(logging.ERROR if _returncode != 0 else logging.INFO, f'Server {self.name} exited with returncode {_returncode}')
        if self.server_ready_task is not None:
            if not self.server_ready_task.done():
                self.server_ready_task.cancel()
                # start_stop_guard is now unlocked by cancelling this task
        self.process = None
        self.active = False
        self.empty_server.set()
        if self.autorestart and self.restart:
            if _returncode != 0:
                self.logger.info(f'Server {self.name} returned non-zero returncode, restart delay applied: {self.autorestart_pause}')
                await asyncio.sleep(self.autorestart_pause)
            self.logger.debug('_reader: restart server')
            await self.launch()
        self.logger.debug('_reader: end')

    async def _timed_restarter(self):
        self.logger.info(f'Timed autorestarter for server {self.name} launched. Interval = {self.timed_autorestart_interval}')
        await asyncio.sleep(self.timed_autorestart_interval)
        try:
            self.logger.info(f'Timed autorestarter for server {self.name} schedules the restart')
            # async with self.restarter_cond:
            #     self.restarter_cond.notify_all()
            await self.stop(timeout=60.0, from_timer=True)
        except RuntimeError:
            pass

    async def _restarter(self):
        self.logger.debug('_restarter: start')
        while self.process is not None:
            self.logger.debug('_restarter: returncode is None')
            async with self.restarter_cond:
                self.logger.debug('_restarter: restarter condition entered')
                await self.restarter_cond.wait()
                self.logger.debug('_restarter: restarter condition received, waiting for empty server')
                self.logger.debug(f'_restarter: lock is currently {self.restarter_cond.locked()}.')
                await self.empty_server.wait()
                self.logger.debug(f'_restarter: lock is currently {self.restarter_cond.locked()}.')
                self.logger.debug('_restarter: restarter condition received, empty server reached')
                await self.stop(timeout=60.0, no_lock=True)
                # self.restarter_cond.release()
                # await self.restarter_cond.acquire()
                self.logger.debug(f'_restarter: lock is currently {self.restarter_cond.locked()}.')

    def handle_stderr(self, line: str):
        self.logger.error(f'STK-Stderr {self.name}: {line}')

    async def handle_stdout(self, line: str):
        if self.ignore_idle.fullmatch(line):
            return
        # handle log message
        _match = self.logstrip.fullmatch(line)
        if _match:
            levelname, objectname, message = _match.groups()
        else:
            # self.logger.info(f'STK [{self.name}] {line[:-1]}')
            if self.show_plain:
                self.writeln(line[:-1])
            return
        if self.joinleave_objectname == objectname:
            _matchjl = self.joinleave_pattern.fullmatch(message)
            if _matchjl:
                _curPeers = int(_matchjl.groups()[0])
                if _curPeers:
                    self.empty_server.clear()
                else:
                    self.empty_server.set()
        level = getattr(logging, levelname.upper(), logging.DEBUG)
        if self.ready_objectname == objectname and self.ready_loglevel == level:
            _matchready = self.ready_pattern.fullmatch(message)
            if _matchready is not None:
                await self.ready_event.emit(int(_matchready.group(1)))
        if not (await self.log_event.emit(message, levelname=levelname, level=level, objectname=objectname)):
            return
        # 'STKHost': {logging.WARNING: [re.compile(r'bad addon: asdasdasd')]}
        if self.global_logignores is not None:
            try:
                for pattern in self.global_logignores[objectname][level]:
                    if pattern.fullmatch(message):
                        # self.logger.debug(f'handle_stdout: skipped line with global pattern {pattern}')
                        return
                    else:
                        # self.logger.debug(f'handle_stdout: global pattern {pattern} didn\'t match: {repr(message)}')
                        pass
            except KeyError:
                pass
        try:
            for pattern in self.log_ignores[objectname][level]:
                if pattern.fullmatch(message):
                    # self.logger.debug(f'handle_stdout: skipped line with pattern {pattern}')
                    return
                else:
                    # self.logger.debug(f'handle_stdout: pattern {pattern} didn\'t match: {repr(message)}')
                    pass
        except KeyError:
            pass
        self.logger.log(level, f'STK [{self.name}] {objectname}: {message}')

    async def stuff(self, cmdline: str, noblock=False):
        _b = cmdline.encode() + b'\n'
        if noblock:
            self.process.stdin.write(_b)
            await self.process.stdin.drain()
        else:
            async with self.interrupting_idle_for():
                self.process.stdin.write(_b)
                await self.process.stdin.drain()

    def idleCancel(self) -> bool:
        if not self.reader_task.done() and self.idle_cancellable:
            self.reader_task.cancel()
            return True
        return False

    @asynccontextmanager
    async def interrupting_idle_for(self):
        """Acquire a lock for interrupting output handler"""
        self.idleCancel()
        try:
            yield await self.lock.acquire()
        finally:
            self.lock.release()

    def save(self, ace: AdminCommandExecutor):
        try:
            export_data = ace.config['servers'][self.name] = {}
            for item in server_attribs:
                _item = getattr(self, item)
                if ace.config.get(item, None) != _item:
                    export_data[item] = _item
            export_data['log_ignores'] = dict(
                (modname, dict((str(level), list(pattern.pattern for pattern in patterns)) for level, patterns in modignores.items()))
                for modname, modignores in self.log_ignores.items()
            )
            ace.save_config()
        except Exception:
            ace.error(traceback.format_exc())


def stkwrapper_command_set(ace: AdminCommandExecutor):
    async def create_server(cmd: AdminCommandExecutor, name: str,
                            cfgpath: Optional[str] = "",
                            datapath: Optional[str] = "",
                            exec_: Optional[str] = "",
                            cwd: Optional[str] = "",
                            autostart: Optional[bool] = False,
                            autorestart: Optional[bool] = False,
                            timed_autorestart: Optional[str] = False,
                            timed_autorestart_interval: Optional[int] = 0,
                            startup_timeout: Optional[float] = None,
                            shutdown_timeout: Optional[float] = None,
                            extra_env: Optional[str] = None,
                            extra_args=tuple()):
        if name in ace.config['servers']:
            cmd.error(f'server {name} already exists, specify another name', log=False)
            return
        cmd.print('Note: for interactive server creation use stk-make-server')
        _kwargs = {}
        for item, name in zip((cfgpath, datapath, exec_, cwd, autostart, autorestart, timed_autorestart,
                               timed_autorestart_interval, startup_timeout, shutdown_timeout,
                               extra_env, extra_args), server_attribs):
            if item:
                _kwargs[name] = item
        try:
            _server = STKServer(ace.logger, ace.ainput.writeln, name, restarter_cond=ace.server_restart_cond, **_kwargs)
        except FileNotFoundError as exc:
            cmd.error(f'Failed, {exc}, re-check the path', log=False)
            return
        _server.save(ace)
        cmd.print(f'Server "{name}" created. To start it, do stk-start {name}')
    ace.add_command(create_server, 'stk-create-server', ((str, 'name'), ),
                    ((str, 'path/to/config.xml'), (str, 'path/to/stk-assets dir'),
                     (str, 'path/to/supertuxkart exec'), (str, 'path/to/server/workdir'),
                     (bool, 'autostart with wrapper?'),
                     (bool, 'autorestart on crash?'), (bool, 'autorestart every n-seconds?'),
                     (int, 'autorestart seconds interval'),
                     (float, 'max startup seconds'), (float, 'max shutdown seconds'),
                     (str, 'extra environment variables'),
                     (None, 'extra arguments space sep')))

    def _startswith_predicate(name: str, stkservername: str):
        return stkservername.startswith(name)

    async def stkserver_tab(cmd: AdminCommandExecutor, name: str = '', *, argl: str):
        if argl:
            return list(ace.servers.keys())
        return list(filter(partial(_startswith_predicate, name), ace.servers.keys()))

    async def make_server(cmd: AdminCommandExecutor, name: str = '', edit_existing=False):
        try:
            (_name_msg, _cfgpath_msg, _assets_msg, _exec_msg,
             _cwd_msg, _as_msg, _ar_msg, _tar_msg, _startt_msg,
             _stopt_msg, _env_msg, _ea_msg) = make_server_msg
            if not name:
                cmd.print(_name_msg)
                while not name:
                    name = await cmd.ainput.prompt_line('name: ', history_disabled=True)
            if edit_existing and name not in ace.servers:
                cmd.error('This server doesn\'t exist', log=False)
                return
            elif not edit_existing and name in ace.servers:
                cmd.error('This server already exists, specify another name', log=False)
                return
            _cwd = os.getcwd()
            if edit_existing:
                _server: STKServer = ace.servers[name]
                _cfgpath_default = _server.cfgpath
                _datapath_default = _server.datapath
                _exec_default = _server.executable_path
                _cwd_default = _server.cwd
                _autostart_default = _server.autostart
                _autorestart_default = _server.autorestart
                _timed_autorestart_interval_default = _server.timed_autorestart_interval
                _startup_timeout_default = _server.startup_timeout
                _shutdown_timeout_default = _server.shutdown_timeout
                _extra_env_default_text = (shlex.join(f"{name}={value}" for name, value in _server.extra_env.items())
                                           if _server.extra_env is not None else None)
                _extra_env_default = _server.extra_env
                _extra_arguments_default = ' '.join(_server.extra_args)
            else:
                _cfgpath_default = cmd.config.get('cfgpath', '')
                _datapath_default = cmd.config.get('datapath', '')
                _exec_default = cmd.config.get('executable_path', '')
                _cwd_default = cmd.config.get('cwd', os.path.join(_cwd, name))
                _autostart_default = cmd.config.get('autostart', False)
                _autorestart_default = cmd.config.get('autorestart', True)
                _timed_autorestart_interval_default = cmd.config.get('timed_autorestart_interval', 0)
                _startup_timeout_default = cmd.config.get('server_startup_timeout', None)
                _shutdown_timeout_default = cmd.config.get('server_startup_timeout', None)
                _extra_env_default = cmd.config.get('extra_env', None)
                _extra_env_default_text = (shlex.join(f"{name}={value}" for name, value in _extra_env_default)
                                           if _extra_env_default is not None else None)
                _extra_arguments_default = cmd.config.get('extra_args', '')
            while True:
                cmd.print(_cwd_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=_cwd_default)))
                cwd = (await cmd.ainput.prompt_line('cwd: ', history_disabled=True)) or _cwd_default
                cwd = os.path.normpath(os.path.abspath(os.path.expanduser(cwd)))
                if os.path.isdir(cwd):
                    break
                else:
                    cmd.print('This directory doesn\'t exist. Create one?')
                    if yes_match.fullmatch(await cmd.ainput.prompt_keystroke(f'create dir "{os.path.abspath(cwd)}"? ')):
                        os.makedirs(cwd)
                        cmd.print('Directory created for server')
                        break
            while True:
                cmd.print(_cfgpath_msg.format(skipmsg=make_server_skipmsg.format(cwd=cwd, default=_cfgpath_default)))
                cfgpath = (await cmd.ainput.prompt_line('cfgpath: ', history_disabled=True)) or _cfgpath_default
                cfgpath = os.path.join(cwd, os.path.expanduser(cfgpath))
                if os.path.isfile(cfgpath):
                    break
                else:
                    cmd.print('This file doesn\'t exist. Skip?')
                    if yes_match.fullmatch(await cmd.ainput.prompt_keystroke('skip? ')):
                        break
            while True:
                cmd.print(_assets_msg.format(skipmsg=make_server_skipmsg.format(cwd=cwd, default=_datapath_default)))
                datapath = (await cmd.ainput.prompt_line('datapath: ', history_disabled=True)) or _datapath_default
                datapath = os.path.normpath(os.path.join(datapath, os.path.expanduser(datapath)))
                if os.path.isdir(datapath):
                    break
                else:
                    cmd.print('This directory doesn\'t exist. Re-check your path')
            while True:
                cmd.print(_exec_msg.format(skipmsg=make_server_skipmsg.format(cwd=cwd, default=_exec_default)))
                executable_path = (await cmd.ainput.prompt_line('exec: ', history_disabled=True)) or _exec_default
                executable_path = os.path.join(cwd, os.path.expanduser(executable_path))
                if os.path.isfile(executable_path):
                    break
                else:
                    cmd.print('This executable doesn\'t exist. Re-check your path')

            # autostart
            cmd.print(_as_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=no_yes[int(_autostart_default)])))
            _autostart = await cmd.ainput.prompt_keystroke('autostart? ')
            if yes_match.fullmatch(_autostart):
                autostart = True
            elif no_match.fullmatch(_autostart):
                autostart = False
            else:
                autostart = _autostart_default

            # autorestart
            cmd.print(_ar_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=no_yes[int(_autorestart_default)])))
            _autorestart = await cmd.ainput.prompt_keystroke('autorestart? ')
            if yes_match.fullmatch(_autorestart):
                autorestart = True
            elif no_match.fullmatch(_autorestart):
                autorestart = False
            else:
                autorestart = _autorestart_default
            cmd.print(_tar_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=_timed_autorestart_interval_default)))

            timed_autorestart_interval = await cmd.ainput.prompt_line('timed autorestart minutes (or empty): ',
                                                                      history_disabled=True)
            if not timed_autorestart_interval or timed_autorestart_interval == '0':
                timed_autorestart = False
                timed_autorestart_interval = 0
            else:
                timed_autorestart = True
                timed_autorestart_interval = int(timed_autorestart_interval) * 60

            cmd.print(_startt_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=_startup_timeout_default)))
            while True:
                try:
                    startup_timeout = await cmd.ainput.prompt_line('startup timeout (n.n): ', history_disabled=True)
                    if not startup_timeout:
                        startup_timeout = _startup_timeout_default
                        break
                    startup_timeout = float(startup_timeout)
                    break
                except ValueError:
                    cmd.print('Specify valid floating point number, for example 120.0')

            cmd.print(_stopt_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=_shutdown_timeout_default)))
            while True:
                try:
                    shutdown_timeout = await cmd.ainput.prompt_line('shutdown timeout (n.n): ', history_disabled=True)
                    if not shutdown_timeout:
                        shutdown_timeout = _shutdown_timeout_default
                        break
                    shutdown_timeout = float(shutdown_timeout)
                    break
                except ValueError:
                    cmd.print('Specify valid floating point number, for example 60.0')

            while True:
                try:
                    cmd.print(_env_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=_extra_env_default_text)))
                    extra_env = await cmd.ainput.prompt_line('extra environment variables: ', history_disabled=True)
                    if not extra_env:
                        extra_env = _extra_env_default
                        break
                    extra_env = dict((name, value) for entity in shlex.split(extra_env)
                                     for name, _, value in (entity.partition('='), ))
                    break
                except ValueError:
                    cmd.print('Specify valid sequence of environment variables, for example: "A=1 B=2 C=something"')

            cmd.print(_ea_msg.format(skipmsg=make_server_skipmsg.format(cwd=_cwd, default=_extra_arguments_default)))
            extra_args = (await cmd.ainput.prompt_line('extra args: ', history_disabled=True)) or _extra_arguments_default
            if not extra_args or extra_args == '-':
                extra_args = tuple()
            else:
                extra_args = splitter.split(extra_args)
            if edit_existing:
                _server: STKServer
                _server.cfgpath = cfgpath
                _server.datapath = datapath
                _server.executable_path = executable_path
                _server.cwd = cwd
                _server.autostart = autostart
                _server.autorestart = autorestart
                _server.timed_autorestart = timed_autorestart
                _server.timed_autorestart_interval = timed_autorestart_interval
                _server.startup_timeout = startup_timeout
                _server.shutdown_timeout = shutdown_timeout
                _server.extra_env = extra_env
                _server.extra_args = extra_args
                _server.save(ace)
                cmd.print('Server successfully edited.')
            else:
                _server = STKServer(
                    cmd.logger, cmd.ainput.writeln, name,
                    cfgpath=cfgpath,
                    datapath=datapath,
                    executable_path=executable_path,
                    cwd=cwd,
                    autostart=autostart,
                    autorestart=autorestart,
                    timed_autorestart=timed_autorestart,
                    timed_autorestart_interval=timed_autorestart_interval,
                    startup_timeout=startup_timeout,
                    shutdown_timeout=shutdown_timeout,
                    restarter_cond=ace.server_restart_cond, start_stop_guard=ace.start_stop_guard,
                    extra_env=extra_env,
                    extra_args=extra_args
                )
                _server.save(ace)
                ace.servers[name] = _server
                cmd.print('Server successfully created. Start it right now?')
                if yes_match.fullmatch(await cmd.ainput.prompt_keystroke(f'start {name}? ')):
                    await _server.launch()
                    cmd.print(f'Starting server {name}')
        except Exception:
            cmd.error(traceback.format_exc(), log=False)
            return
    ace.add_command(partial(make_server, edit_existing=False), 'stk-make-server', optargs=((str, 'name'), ),
                    description='Interactive command for registering STK servers into the wrapper.')
    ace.add_command(partial(make_server, edit_existing=True), 'stk-edit-server', args=((str, 'name'), ),
                    description='Interactive command for editing STK servers in case you messed up.',
                    atabcomplete=stkserver_tab)

    async def start_server(cmd: AdminCommandExecutor, name: str, autorestart: bool = None):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        if _server.active:
            cmd.error(f'Server {name} is already running. To stop it, do stk-stop {name}', log=False)
            return
        _tsk = asyncio.create_task(_server.launch())
        ace.tasks[_tsk.get_name()] = _tsk
        cmd.print(f'Starting STK Server {name}')
    ace.add_command(start_server, 'stk-start', ((str, 'name'), ), ((bool, 'autorestart?'), ), 'Launch an STK server', stkserver_tab)

    async def stop_server(cmd: AdminCommandExecutor, name: str, force=False, timeout: Optional[float] = None):
        if timeout is None:
            timeout = cmd.config['server_shutdown_timeout']
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        if not _server.active:
            cmd.error(f'Server {name} is already stopped. To start it, do stk-start {name}', log=False)
            return
        if not _server.empty_server.is_set() and not force:
            cmd.error(f'Server {name} currently has players. To stop it anyway, specify second argument as yes', log=False)
            return
        _server.restart = False
        _tsk = asyncio.create_task(_server.stop(timeout))
        ace.tasks[_tsk.get_name()] = _tsk
        cmd.print(f'Stopping server {name}')
    ace.add_command(stop_server, 'stk-stop', ((str, 'name'), ), ((bool, 'even if players'), (float, 'timeout'), ), 'Stop an STK server. When timeout reaches, the process is killed', stkserver_tab)

    async def restart_server(cmd: AdminCommandExecutor, name: str, force=False):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        if not _server.active:
            cmd.error(f'Server {name} is already stopped. To start it, do stk-start {name}', log=False)
            return
        if not _server.empty_server.is_set() and not force:
            cmd.error(f'Server {name} currently has players. To stop it anyway, specify second argument as yes', log=False)
            return
        _server.restart = True
        _tsk = asyncio.create_task(_server.stop(60))
        ace.tasks[_tsk.get_name()] = _tsk
        cmd.print(f'Restarting server {name}')
    ace.add_command(restart_server, 'stk-restart', ((str, 'name'), ), ((bool, 'force'), ), 'Restart an STK server.', stkserver_tab)

    async def server_ncsend(cmd, name: str, line: str):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        if not _server.active:
            cmd.error(f'Server {name} is stopped. To start it, do stk-start {name}', log=False)
            return
        await _server.stuff(line)
    ace.add_command(server_ncsend, 'stk-cmd', ((str, 'name'), (None, 'cmd')), description='Send a command to STK server', atabcomplete=stkserver_tab)

    async def server_enternc(cmd: AdminCommandExecutor, name: str, quitword: str = 'quit'):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        if not _server.active:
            cmd.error(f'Server {name} is stopped. To start it, do stk-start {name}', log=False)
            return
        quitword = '.' + quitword
        _prompt = f'{name}> '
        cmd.ainput.writeln(f'Entered "network console" for {name}. Type {quitword} to return back to command prompt', fgcolor=colors.YELLOW)
        _server.show_plain = True
        while True:
            line = await cmd.ainput.prompt_line(_prompt, prompt_formats={'fgcolor': colors.YELLOW}, input_formats={'fgcolor': 14})
            if line == quitword:
                break
            await _server.stuff(line)
        _server.show_plain = False
    ace.add_command(server_enternc, 'stk-nc', ((str, 'name'), ), ((None, 'quitword'), ), 'Enter into interactive network console of the server. To return back, send ".quit".', stkserver_tab)

    async def list_servers(cmd, cpage: int = 1):
        ace.servers: Mapping[str, STKServer]
        _len = len(ace.servers)
        _maxpage, _start, _end = paginate_range(_len, 10, cpage)
        cmd.print(f'STK servers: (page {cpage} or {_maxpage})')
        cmd.print('\n'.join(f'{name}: pid {getattr(server.process, "pid", -1)}' for name, server in tuple(ace.servers.items())[_start:_end]))
    ace.add_command(list_servers, 'stk-servers', optargs=((int, 'page'), ))

    async def server_norestart(cmd: AdminCommandExecutor, name: str):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        if _server.timer_task is not None:
            if not _server.timer_task.done():
                _server.timer_task.cancel()
                cmd.print(f'Timer task has been killed for {name}')
        if _server.autorestart:
            cmd.print(f'Autorestart for server "{name}" has been disabled.')
            _server.autorestart = False
        else:
            cmd.print(f'Autorestart for server "{name}" has been enabled')
            _server.autorestart = True
    ace.add_command(server_norestart, 'stk-norestart', ((str, 'name'), ), description='Stop autorestart timer for STK server')

    async def server_timedrestart(cmd: AdminCommandExecutor, name: str, interval_mins: int):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        if _server.timer_task is not None:
            if not _server.timer_task.done():
                _server.timer_task.cancel()
                cmd.print(f'Timer task has been killed for {name}')
        _server.timed_autorestart = True
        _server.timed_autorestart_interval = interval_mins * 60
        _server.timer_task = asyncio.create_task(_server._timed_restarter())
        cmd.print(f'Restarter enabled with {interval_mins} minutes.')
    ace.add_command(server_timedrestart, 'stk-timed-restart', ((str, 'name'), (int, 'interval_mins')), description='Enable autorestart timer for STK server')

    async def stk_stopall(cmd: AdminCommandExecutor):
        cmd.print('Stopping all the servers forcefully')
        for server in ace.servers:
            if server.active:
                server.restart = False
                _tsk = asyncio.create_task(server.stop())
                ace.tasks[_tsk.get_name()] = _tsk
    ace.add_command(stk_stopall, 'stk-stopall', description='Stops all servers')

    async def wrapper_reloadcfg(cmd: AdminCommandExecutor, full=False):
        if full:
            for server in ace.servers.values():
                server.restart = False
            await asyncio.gather(*(server.stop(10) for server in ace.servers.values() if server.active))
            ace.servers.clear()
        load_config()
        cmd.print('Configuration reloaded and changes are reverted.')
    ace.add_command(wrapper_reloadcfg, 'reloadcfg', optargs=((bool, 'hard reload?'), ),
                    description='Reloads config.json. When hard reload is enabled, turns all servers off within 10 seconds')

    async def list_globallogignore(cmd: AdminCommandExecutor, modname: str, levelname: str, cpage=1):
        level = LogLevel[levelname.upper()].value
        if modname in ace.global_logignores:
            if level in ace.global_logignores[modname]:
                _logignores = ace.global_logignores[modname][level]
                _len = len(_logignores)
                _maxpage, _start, _end = paginate_range(_len, 10, cpage)
                cmd.print(f'Global Log-Ignore patterns (page {cpage} of {_maxpage}):')
                cmd.print(*(f'#{i}: {logignore.pattern}' for i, logignore in ((i, _logignores[i]) for i in range(_start, _end))), sep='\n')
                return
        cmd.print(f'No Log-Ignores exist for {modname}: {level}')
    ace.add_command(list_globallogignore, 'stk-global-logignores', ((str, 'object name'), (str, 'levelname')), ((int, 'page'), ), 'Shows the list of filters for module (or log object) and log level.')

    async def list_globallogignorelevels(cmd: AdminCommandExecutor, modname: str):
        if modname not in ace.global_logignores:
            cmd.print(f'No Log-Ignores exist for {modname}')
            return
        _loglevels = (LogLevel(i).name for i in ace.global_logignores[modname].keys())
        cmd.print(f'Next levels exist for {modname}:\n{", ".join(_loglevels)}')
    ace.add_command(list_globallogignorelevels, 'stk-global-logignore-levels', ((str, 'object name'), ), description='Shows the list of levels that exists for a specific log-object')

    async def list_globallogignoreobjects(cmd: AdminCommandExecutor):
        cmd.print(f'Next logobjects (or modules) exist in Log-Ignore:\n{", ".join(ace.global_logignores.keys())}')
    ace.add_command(list_globallogignoreobjects, 'stk-global-logignore-objects', description='Shows which log objects are registered in Log-Ignore')

    async def list_logignore(cmd: AdminCommandExecutor, name: str, modname: str, levelname: str, cpage=1):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        level = LogLevel[levelname.upper()].value
        all_log_ignores = _server.log_ignores.maps[0]
        if modname in all_log_ignores:
            if level in all_log_ignores[modname]:
                _logignores = all_log_ignores[modname][level]
                _len = len(_logignores)
                _maxpage, _start, _end = paginate_range(_len, 10, cpage)
                cmd.print(f'Log-Ignores for server {name} (logobject {modname}: {level}) (page {cpage} of {_maxpage}):')
                cmd.print(*(f'#{i}: {logignore.pattern}' for i, logignore in ((i, _logignores[i]) for i in range(_start, _end))), sep='\n')
                return
        cmd.print(f'No Log-Ignores exist for {modname}: {level} at server {name}')
    ace.add_command(list_logignore, 'stk-logignores',
                    ((str, 'server name'), (str, 'logobject'), (str, 'loglevel')), ((int, 'page'), ),
                    description='Shows the Log-Ignore patterns of logobject and loglevel for specific STK server.',
                    atabcomplete=stkserver_tab)

    async def list_logignorelevels(cmd: AdminCommandExecutor, name: str, modname: str):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        all_log_ignores = _server.log_ignores.maps[0]
        _levels = ', '.join(LogLevel(i).name for i in all_log_ignores[modname].keys())
        if modname in all_log_ignores:
            cmd.print(f"Next Log-Ignores exist for {modname} at server {name}:\n{_levels}")
            return
        cmd.print(f'No Log-Ignores exist for {modname} at server {name}')
    ace.add_command(list_logignorelevels, 'stk-logignore-levels',
                    ((str, 'server name'), (str, 'logobject')),
                    description='Shows the list of existing Log-Ignore levels in a logobject for a specific STK server',
                    atabcomplete=stkserver_tab)

    async def list_logignoreobjects(cmd: AdminCommandExecutor, name: str):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        all_log_ignores = _server.log_ignores.maps[0]
        _modnames = ', '.join(all_log_ignores.keys())
        cmd.print(f'Next log objects exist in Log-Ignore of a server {name}\n{_modnames}')
    ace.add_command(list_logignoreobjects, 'stk-logignore-objects', ((str, 'server name'), ),
                    description='Shows the list of existing log objects in Log-Ignore of a specific STK server',
                    atabcomplete=stkserver_tab)

    async def globallogignore_add(cmd: AdminCommandExecutor, modname: str, levelname: str, pattern: str):
        level = LogLevel[levelname.upper()].value
        strlevel = str(level)
        _pattern = re.compile(pattern)
        cfg_gli = ace.config['global_logignores']
        if modname not in cfg_gli:
            cfg_gli[modname] = {strlevel: [pattern]}
            ace.global_logignores[modname] = {level: [_pattern]}
        elif strlevel not in cfg_gli[modname]:
            cfg_gli[modname][strlevel] = [pattern]
            ace.global_logignores[modname][level] = [_pattern]
        elif pattern not in cfg_gli[modname][strlevel]:
            cfg_gli[modname][strlevel].append(pattern)
            ace.global_logignores[modname][level].append(_pattern)
        else:
            cmd.error('This exact pattern already exists in the list.', log=False)
            return
        ace.save_config()
        cmd.print(f'Pattern added to the list for {modname}: {levelname}')
    ace.add_command(globallogignore_add, 'stk-global-logignore-add', ((str, 'logobject'), (str, 'levelname'), (None, 'pattern')),
                    description='Add Python regex pattern to the global Log-Ignore list.')

    async def globallogignore_del(cmd: AdminCommandExecutor, modname: str, levelname: str, id_: int):
        level = LogLevel[levelname.upper()].value
        strlevel = str(level)
        cfg_gli = ace.config['global_logignores']
        if modname not in cfg_gli:
            cmd.error('This logobject does not exist in global Log-Ignore.', log=False)
            return
        elif strlevel not in cfg_gli[modname]:
            cmd.error('This level does not exist for logobject in global Log-Ignore.', log=False)
            return
        elif id_ >= len(cfg_gli[modname][strlevel]) or id_ < 0:
            cmd.error('Id is not valid. Use stk-global-logignores to check the id', log=False)
            return
        else:
            _pattern = ace.global_logignores[modname][level][id_].pattern
            del ace.global_logignores[modname][level][id_]
            del cfg_gli[modname][strlevel][id_]
            ace.save_config()
            cmd.print(f'Pattern "{_pattern}" deleted.')
    ace.add_command(globallogignore_del, 'stk-global-logignore-del', ((str, 'logobject'), (str, 'levelname'), (int, 'id')),
                    description='Delete Python regex from the Log-Ignore list')

    async def globallogignore_dellevel(cmd: AdminCommandExecutor, modname: str, levelname: str):
        level = LogLevel[levelname.upper()].value
        strlevel = str(level)
        cfg_gli = ace.config['global_logignores']
        if modname not in cfg_gli:
            cmd.error('This logobject does not exist in global Log-Ignore.', log=False)
        elif strlevel not in cfg_gli[modname]:
            cmd.error('This level does not exist for logobject in global Log-Ignore.', log=False)
        else:
            del ace.global_logignores[modname][level]
            del cfg_gli[modname][strlevel]
            ace.save_config()
            cmd.print(f'Level {level} deleted.')
    ace.add_command(globallogignore_dellevel, 'stk-global-logignore-dellevel', ((str, 'logobject'), (str, 'levelname')),
                    description='Delete the whole level from global logobject')

    async def globallogignore_delmod(cmd: AdminCommandExecutor, modname: str):
        cfg_gli = ace.config['global_logignores']
        if modname not in cfg_gli:
            cmd.error('This logobject does not exist in global Log-Ignore.', log=False)
            return
        else:
            del ace.global_logignores[modname]
            del cfg_gli[modname]
            ace.save_config()
            cmd.print(f'LogObject {modname} deleted.')
    ace.add_command(globallogignore_delmod, 'stk-global-logignore-delobj', ((str, 'logobject'), ),
                    description='Delete the whole log object from global Log-Ignore')

    async def logignore_add(cmd: AdminCommandExecutor, name: str, modname: str, levelname: str, pattern: str):
        level = LogLevel[levelname.upper()].value
        strlevel = str(level)
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        all_log_ignores = _server.log_ignores.maps[0]
        _pattern = re.compile(pattern)
        cfg_li = ace.config['servers'][name]['log_ignores']
        if modname not in cfg_li:
            cfg_li[modname] = {strlevel: [pattern]}
            all_log_ignores[modname] = {level: [_pattern]}
        elif strlevel not in cfg_li[modname]:
            cfg_li[modname][strlevel] = [pattern]
            all_log_ignores[level] = [_pattern]
        elif pattern not in cfg_li[modname][strlevel]:
            cfg_li[modname][strlevel].append(pattern)
            all_log_ignores[modname][level].append(_pattern)
        else:
            cmd.error('This exact pattern already exists in the list.', log=False)
            return
        ace.save_config()
        cmd.print(f'Pattern added to the list for {modname}: {levelname}')
    ace.add_command(logignore_add, 'stk-logignore-add',
                    ((str, 'server name'), (str, 'logobject'), (str, 'levelname'), (None, 'pattern')),
                    description='Add Python regex pattern to the Log-Ignore list of specific STK server.',
                    atabcomplete=stkserver_tab)

    async def logignore_del(cmd: AdminCommandExecutor, name: str, modname: str, levelname: str, id_: int):
        try:
            level = LogLevel[levelname.upper()].value
            strlevel = str(level)
            if name not in ace.servers:
                cmd.error('Server doesn\'t exist', log=False)
                return
            _server: STKServer = ace.servers[name]
            all_log_ignores = _server.log_ignores.maps[0]
            cfg_li = ace.config['servers'][name]['log_ignores']
            if modname not in cfg_li:
                cmd.error('This logobject does not exist in Log-Ignore.', log=False)
                return
            elif strlevel not in cfg_li[modname]:
                cmd.error('This level does not exist for logobject in Log-Ignore.', log=False)
                return
            elif id_ >= len(cfg_li[modname][strlevel]) or id_ < 0:
                cmd.error(f'Id is not valid. Use stk-logignores {name} to check the id', log=False)
                return
            else:
                _pattern = all_log_ignores[modname][level][id_].pattern
                del all_log_ignores[modname][level][id_]
                del cfg_li[modname][strlevel][id_]
                ace.save_config()
                cmd.print(f'Pattern "{_pattern}" deleted.')
        except Exception:
            cmd.error(traceback.format_exc(), log=False)
            return
    ace.add_command(logignore_del, 'stk-logignore-del',
                    ((str, 'server name'), (str, 'logobject'), (str, 'levelname'), (int, 'id')),
                    description='Delete Python regex from the Log-Ignore list',
                    atabcomplete=stkserver_tab)

    async def logignore_dellevel(cmd: AdminCommandExecutor, name: str, modname: str, levelname: str):
        level = LogLevel[levelname.upper()].value
        strlevel = str(level)
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        all_log_ignores = _server.log_ignores.maps[0]
        cfg_li = ace.config['servers'][name]['log_ignores']
        if modname not in cfg_li:
            cmd.error('This logobject does not exist in Log-Ignore.', log=False)
            return
        elif strlevel not in cfg_li[modname]:
            cmd.error('This level does not exist for logobject in Log-Ignore.', log=False)
            return
        else:
            del all_log_ignores[modname][level]
            del cfg_li[modname][strlevel]
            ace.save_config()
            cmd.print(f'Level {level} deleted.')
    ace.add_command(logignore_dellevel, 'stk-logignore-dellevel',
                    ((str, 'server name'), (str, 'logobject'), (str, 'levelname')),
                    description='Delete the whole level from logobject Log-Ignore',
                    atabcomplete=stkserver_tab)

    async def logignore_delmod(cmd: AdminCommandExecutor, name: str, modname: str):
        if name not in ace.servers:
            cmd.error('Server doesn\'t exist', log=False)
            return
        _server: STKServer = ace.servers[name]
        all_log_ignores = _server.log_ignores.maps[0]
        cfg_li = ace.config['servers'][name]['log_ignores']
        if modname not in cfg_li:
            cmd.error('This logobject does not exist in Log-Ignore.', log=False)
            return
        else:
            del all_log_ignores[modname]
            del cfg_li[modname]
            ace.save_config()
            cmd.print(f'LogObject {modname} deleted.')
    ace.add_command(logignore_delmod, 'stk-logignore-delobj',
                    ((str, 'server name'), (str, 'logobject')),
                    description='Delete the whole log object from Log-Ignore of specific STK server',
                    atabcomplete=stkserver_tab)


async def _cleanup_servers(ace: AdminCommandExecutor):
    for server in ace.servers.values():
        server.restart = False
    await asyncio.gather(*(server.stop(10, no_lock=True) for server in ace.servers.values() if server.active))


async def main():
    if not os.path.isfile(_cfgfile_path):
        with open(_cfgfile_path, 'x') as file:
            file.write('{}')
    ace = AdminCommandExecutor({}, logger=logging.getLogger('STKServerWrapper'))
    ace.full_cleanup_steps.add(_cleanup_servers)
    restarter_cond = ace.server_restart_cond = asyncio.Condition()
    start_stop_guard = ace.start_stop_guard = asyncio.Lock()
    ace.server_restart_clk = partial(server_restart_clk, ace)
    ace.servers: MutableMapping[str, STKServer] = {}
    _servers_to_start = []
    _ver = ace.config['stk_version'] = ace.config.get('stk_version', '1.3.0')
    _logpath = ace.config['logpath'] = ace.config.get('logpath', 'logs')
    _servers = ace.config['servers'] = ace.config.get('servers', {})
    ace.config['datapath'] = ace.config.get('datapath', 'stk-code')
    ace.config['executable_path'] = ace.config.get('executable_path', 'supertuxkart')
    ace.config['autostart'] = ace.config.get('autostart', False)
    ace.config['autorestart'] = ace.config.get('autorestart', True)
    ace.config['autorestart_pause'] = ace.config.get('autorestart_pause', 10.0)
    ace.config['timed_autorestart'] = ace.config.get('timed_autorestart', False)
    ace.config['timed_autorestart_interval'] = ace.config.get('timed_autorestart_interval', False)
    ace.config['startup_timeout'] = ace.config.get('startup_timeout', 120.0)
    ace.config['shutdown_timeout'] = ace.config.get('shutdown_timeout', 120.0)
    ace.config['extra_env'] = ace.config.get('extra_env', None)
    ace.config['extra_args'] = ace.config.get('extra_args', [])  # json doesn't support immutable sequences, use mutable instead
    _server_shutdown_timeout = ace.config.get('server_shutdown_timeout', 60.0)
    if _server_shutdown_timeout < 0:
        _server_shutdown_timeout = None
    ace.config['server_shutdown_timeout'] = _server_shutdown_timeout
    _server_startup_timeout = ace.config.get('server_startup_timeout', 120.0)

    if _server_startup_timeout < 0:
        _server_startup_timeout = None

    ace.config['server_startup_timeout'] = _server_startup_timeout
    _global_logignores = ace.config['global_logignores'] = ace.config.get('global_logignores', {})
    ace.global_logignores = make_logignores(_global_logignores)
    ace.save_config()
    ace.stuff['stk_version'] = parseVersion(_ver)
    print(f'Initializing logging. Configured STK version is {_ver}.')
    ace.logger.setLevel(logging.INFO)
    date_format = '%Y-%m-%d %H:%M:%S'
    time_format = '%H:%M:%S'
    record_format = '%(asctime)s [%(levelname)s] %(message)s'
    stdout_handler = ARILogHandler(ace.ainput)
    stdout_handler.setFormatter(
        logging.Formatter(record_format, date_format)
    )
    stdout_handler.setLevel(logging.DEBUG)
    if not os.path.isdir(_logpath):
        os.mkdir(_logpath)
    file_handler = TimedRotatingFileHandler(os.path.join(_logpath, 'stkserver-wrapper'), 'midnight',
                                            backupCount=180)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(record_format, time_format)
    )
    ace.logger.addHandler(file_handler)
    ace.logger.addHandler(stdout_handler)
    print('Loading server list...')
    for servername, serverdata in _servers.items():
        # cwd: str, autorestart=True, timed_autorestart=False,
        # timed_autorestart_interval: Optional[float] = None,
        # restarter_cond: Optional[asyncio.Condition] = None,
        # extra_args: Optional[Sequence[str]] = tuple()):
        server = ace.servers[servername] = STKServer(
            ace.logger, ace.ainput.writeln, servername, cfgpath=serverdata['cfgpath'],
            restarter_cond=restarter_cond, start_stop_guard=start_stop_guard,
            datapath=serverdata.get('datapath', ace.config['datapath']),
            executable_path=serverdata.get('executable_path', ace.config['executable_path']),
            cwd=serverdata.get('cwd', ace.config.get('cwd', os.getcwd())),
            autostart=serverdata.get('autostart', ace.config['autostart']),
            autorestart=serverdata.get('autorestart', ace.config['autorestart']),
            autorestart_pause=serverdata.get('autorestart_pause', ace.config['autorestart_pause']),
            timed_autorestart=serverdata.get('timed_autorestart', ace.config['timed_autorestart']),
            timed_autorestart_interval=serverdata.get('timed_autorestart_interval', ace.config['timed_autorestart_interval']),
            startup_timeout=serverdata.get('startup_timeout', ace.config.get('startup_timeout', 120.0)),
            shutdown_timeout=serverdata.get('shutdown_timeout', ace.config.get('shutdown_timeout', 120.0)),
            extra_env=serverdata.get('extra_env', ace.config.get('extra_env', None)),
            extra_args=serverdata.get('extra_args', ace.config.get('extra_args', tuple())),
            global_logignores=ace.global_logignores,
            logignores=make_logignores(serverdata.get('log_ignores', {}))
        )
        if server.autostart:
            _servers_to_start.append(server)
    basic_command_set(ace)
    stkwrapper_command_set(ace)
    await ace.load_extensions()
    ace.promptheader = '-=STK=-'
    ace.promptarrow = ':'
    ace.prompt_format = {'fgcolor': colors.GREEN}
    ace.input_format = {'fgcolor': colors.WHITE}
    # autostarting servers that has autostart enabled
    for server in _servers_to_start:
        ace.print(f'Autostarting server {server.name}...')
        _tsk = asyncio.create_task(server.launch())
        ace.tasks[_tsk.get_name()] = _tsk
    return await ace.prompt_loop()


async def extension_init(self: AdminCommandExtension):
    self.msg('This module should not be used as an extension.\n'
             'Use it as bare module for patching up the functions')


async def extension_cleanup(self: AdminCommandExtension):
    self.msg('bye')


if __name__ == '__main__':
    asyncio.run(main())
