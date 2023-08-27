import asyncio
import html
import os
import re
import traceback
import logging
from configparser import ConfigParser
from shutil import rmtree
from zipfile import ZipFile
from math import floor
from aiohttp import ClientSession, ClientResponse
from admin_console import AdminCommandExecutor, AdminCommandExtension, paginate_range
from aiohndchain import AIOHandlerChain
from defusedxml import ElementTree as dElementTree
from xml.etree import ElementTree
from enum import Flag, Enum
from itertools import islice, repeat, chain, count
from packaging.version import parse as parseVersion, InvalidVersion
from typing import Sequence, Tuple, MutableSequence


black_star = chr(9733)
white_star = chr(9734)
dot = '.'
kart_l = 'kart'
track_l = 'track'
soccer_l = 'soccer'
arena_l = 'arena'
ctf_l = 'ctf'
false_l = 'false'
Y_l = 'Y'
N_l = 'N'
empty_str = ''
delimiter = re.compile(r'[,./;: ] *')
nostatus = re.compile(r'(?:-)[a-zA-Z_]+')
status = re.compile(r'(?:\+)?[a-zA-Z_]+')
stripper = re.compile(r'[+-]')
heavy_check_sign = chr(9989)
defaultconf = {
    'AddonUpdater': {
        'online_assets_url': 'https://online.supertuxkart.net/downloads/xml/online_assets.xml',
        'fetch_karts': False,
        'autoupdate': True,
        'autoupdate_interval': 3600 * 6,
        'autoupdate_banlist': "",
        'autoinstall': True,
        'autoinstall_karts': False,
        'autoinstall_minrating': 1.0,
        'autoinstall_requirements': "+APPROVED,+DFSG,-ALPHA",
        'autoinstall_banlist': "",
        'downloadpath': 'downloads',
        'addonpath': os.path.expanduser('~/.local/share/supertuxkart/addons')
    }
}


class AddonStatus(Flag):
    APPROVED = 0x0001
    ALPHA = 0x0002
    BETA = 0x0004
    RC = 0x0008
    INVISIBLE = 0x0010
    HQ = 0x0020
    DFSG = 0x0040
    FEATURED = 0x0080
    LATEST = 0x0100
    BAD_DIM = 0x0200

    def _predicate(self, item: Enum):
        return bool(self.value & item.value)

    def _strict_predicate(self, item: Enum):
        return self.value & item.value == self.value

    def _xml_predicate(self, item: ElementTree.Element):
        return self.value & int(item.attrib['status']) == self.value

    @staticmethod
    def _xml_allowdeny_predicate(triple: Tuple[ElementTree.Element, int, int]):
        element, allow, deny = triple
        status_ = int(element.attrib['status'])
        if status_ & deny:
            return False
        return status_ & allow

    def __iter__(self):
        return filter(self._predicate, self.__class__.__members__.values())

    @classmethod
    def from_str(cls, flags: str):
        flags_ = stripper.sub('', flags.upper())
        return cls(sum(cls[item].value for item in delimiter.split(flags_)))

    @classmethod
    def allowdeny_pair(cls, flags: str) -> Tuple[int, int]:
        flags_ = delimiter.split(flags.upper())
        return cls.strlist_allowed(flags_), cls.strlist_denied(flags_)

    @classmethod
    def strlist_allowed(cls, flags: Sequence[str]) -> int:
        return sum(cls[stripper.sub('', item)].value for item in filter(status.fullmatch, flags)) or sum(x.value for x in cls)

    @classmethod
    def strlist_denied(cls, flags: Sequence[str]) -> int:
        return sum(cls[stripper.sub('', item)].value for item in filter(nostatus.fullmatch, flags))


def ratingStars(rating: float) -> str:
    return ''.join(islice(chain(repeat(black_star, int(rating * 2)), repeat(white_star)), 6))


def flags(value: str):
    return ', '.join(item.name.title() for item in AddonStatus(int(value)))


def classifyAddon(addonInfo: ElementTree.Element) -> Sequence[str]:
    if addonInfo.tag == track_l:
        tags = []
        _track = True
        if addonInfo.attrib.get(soccer_l, N_l) == Y_l:
            tags.append(soccer_l)
            _track = False
        if addonInfo.attrib.get(arena_l, N_l) == Y_l:
            tags.append(arena_l)
            _track = False
            if addonInfo.attrib.get(ctf_l, N_l) == Y_l:
                tags.append(ctf_l)
        if _track:
            tags.append(track_l)
        return tags
    if addonInfo.attrib.get(ctf_l, N_l) == Y_l:
        return [addonInfo.tag, ctf_l]
    return [addonInfo.tag]


def tab_complete_addontype(arg: str):
    _res = []
    if track_l.startswith(arg):
        _res.append(track_l)
    if soccer_l.startswith(arg):
        _res.append(soccer_l)
    if arena_l.startswith(arg):
        _res.append(arena_l)
    if ctf_l.startswith(arg):
        _res.append(ctf_l)
    return _res


def tab_complete_onlineaddon(ext: AdminCommandExtension, arg: str):
    def _predicate(target: str):
        return target.startswith(arg)
    return list(filter(_predicate, ext.data['addons_dict'].keys()))


def tab_complete_installedaddon(ext: AdminCommandExtension, arg: str):
    def _predicate(target: str):
        return target.startswith(arg)
    return list(filter(_predicate, ext.data['installed_dict'].keys()))


async def fetch(ext: AdminCommandExtension, check_updates=True):
    fetch_karts = ext.mconfig.getboolean('fetch_karts')
    if 'addons_tree' not in ext.data:
        ext.logger.info('Fetching online_addons for the first time...')
    else:
        ext.logger.info('Fetching online_addons again...')
    online_assets_url = ext.mconfig['online_assets_url']
    clientsession: ClientSession = ext.clientsession
    async with clientsession.get(online_assets_url) as resp:
        data = await resp.text()
    element = dElementTree.fromstring(data)
    tree = ElementTree.ElementTree(element)
    ext.data['addons_tree'] = tree
    addons_dict = ext.data['addons_dict'] = {}
    ext.logger.info(f'Fetched data size is {len(data)}, {len(element)} entries')
    if check_updates and 'installed_dict' not in ext.data:
        ext.logger.warning("Cannot check for updates: installed addons haven't been checked before fetching online addons")
        check_updates = False
        updates_available = None
    elif check_updates and 'updates_available' not in ext.data:
        updates_available = ext.data['updates_available'] = []
    elif check_updates:
        updates_available = ext.data['updates_available']
    else:
        return
    for i in range(len(element) - 1, -1, -1):
        try:
            addon = element[i]
            _rawMinVer = addon.attrib['min-include-version']
            _rawMaxVer = addon.attrib['max-include-version']
            _curVer = ext.ace.stuff['stk_version']
            _minVer = parseVersion(_rawMinVer) if _rawMinVer else _curVer
            _maxVer = parseVersion(_rawMaxVer) if _rawMaxVer else _curVer
            if (_minVer > _curVer and _rawMinVer) or (_rawMaxVer and _curVer > _maxVer):
                del element[i]
                ext.logger.debug(f'Found incompatible addon "{addon.attrib["name"]}" id={addon.attrib["id"]} which has version {_minVer}-{_maxVer}')
                continue
            if addon.tag == 'kart' and not fetch_karts:
                del element[i]
                ext.logger.debug(f'Skipping kart "{addon.attrib["name"]} id={addon.attrib["id"]}"')
                continue
            # benau moment...
            # if AddonStatus.LATEST not in AddonStatus(int(addon.attrib['status'])):
            #     ext.logger.debug(f'Skipping non-latest "{addon.attrib["name"]} id={addon.attrib["id"]}"')
            #     del element[i]
            #     continue
            # instead, just manually check revision number
            if addon.attrib['id'] in addons_dict:
                taddon = addons_dict[addon.attrib['id']]
                if int(addon.attrib['revision']) < int(taddon.attrib['revision']):
                    continue
            addons_dict[addon.attrib['id']] = addon
            if check_updates and addon.attrib['id'] in ext.data['installed_dict']:
                try:
                    _installed = ext.data['installed_dict'][addon.attrib['id']]
                    _local_revision = int(_installed.attrib['revision'])
                    _remote_revision = int(addon.attrib['revision'])
                    if _remote_revision > _local_revision:  # did you know that str can be compared exactly as int? NO
                        ext.logger.info(f"{addon.attrib['name']} id={addon.attrib['id']} can be updated from rev{_local_revision} to rev{_remote_revision}")
                        updates_available.append(addon)
                except KeyError as exc:
                    ext.logger.error(f"Failed to check available update for {addon.attrib['name']}, missing {exc}")
        except InvalidVersion as exc:
            ext.logger.warning(f"Addon {addon.attrib['name']} has an invalid version: {repr(exc)}")
    ext.logger.info('Operation complete')


def fetch_installed(ext: AdminCommandExtension):
    fetch_karts = ext.mconfig.getboolean('fetch_karts')
    ext.logger.info('Retrieving local addons...')
    if 'installed_addons' not in ext.data:
        installed_addons = ext.data['installed_addons'] = ElementTree.Element('installed-addons')
    else:
        installed_addons = ext.data['installed_addons']
    for item in ('installed_dict', track_l, soccer_l, arena_l, kart_l):
        if item not in ext.data:
            installed_dict = ext.data[item] = {}
    installed_dict = ext.data['installed_dict']
    track_addons = ext.data[track_l]
    soccer_addons = ext.data[soccer_l]
    arena_addons = ext.data[arena_l]
    kart_addons = ext.data[kart_l]
    # generate xml and dictionary
    for addontype in (track_l, kart_l):
        with os.scandir(os.path.join(os.path.expanduser(ext.mconfig['addonpath']), addontype + 's')) as scandir:
            for addon_dir in scandir:
                if addon_dir.is_dir():
                    # can load information about this addon
                    _xmlname = f'{addontype}.xml'
                    _xml_path = os.path.join(addon_dir.path, _xmlname)
                    if not os.path.isfile(_xml_path):
                        ext.logger.error(f"Addon {addon_dir.name} doesn't have {_xmlname}, cannot load into known addons list")
                        continue
                    try:
                        _xml = ElementTree.parse(_xml_path).getroot()
                    except ElementTree.ParseError as exc:
                        ext.logger.debug(f'Cannot load addon data for {addon_dir.name}: {exc}')
                        continue
                    types_ = classifyAddon(_xml)
                    if track_l in types_:
                        track_addons[addon_dir.name] = _xml
                    if soccer_l in types_:
                        soccer_addons[addon_dir.name] = _xml
                    if arena_l in types_:
                        arena_addons[addon_dir.name] = _xml
                    if kart_l in types_:
                        kart_addons[addon_dir.name] = _xml
                    installed_addons.append(_xml)
                    installed_dict[addon_dir.name] = _xml
        if not fetch_karts:
            break
    ext.logger.info('Local addons retrieved')


async def download_addon(ext: AdminCommandExtension, addonid: str, download_link: str) -> str:
    # _installed: ElementTree.Element = ext.data['installed_dict'][addonid]
    clientsession: ClientSession = ext.clientsession
    # ext.logger.info(f'Updating addon {addonid} from rev{_installed.attrib.get("revision", "?")}'
    #                 f' to rev{_addon.attrib["revision"]}, '
    #                 f'download link is {_download_link}.')
    _download_path = ext.mconfig['downloadpath']
    _filepath = os.path.join(_download_path, f'{addonid}.zip')
    ext.logger.info(f'Downloading {addonid} from {download_link} to {_filepath}...')
    if os.path.isfile(_filepath):
        ext.logger.info(f'Deleting previous version file {_filepath}')
        _mode = 'wb'
    else:
        _mode = 'xb'
    with open(_filepath, _mode) as file:
        _downloaded_bytes = 0
        async with clientsession.get(download_link) as resp:
            resp: ClientResponse
            _length = resp.content_length
            _length_kb = (_length or 0) / 1024
            ext.logger.info(f'{addonid}: downloadable zip file is {_length_kb} kb of data')
            counter = count()
            async for data in resp.content.iter_any():
                file.write(data)
                _downloaded_bytes += len(data)
                _progress = floor(_downloaded_bytes / _length * 100)
                if _progress == 100 or not (next(counter) % 16):
                    ext.logger.info(f'{addonid}: download progress {_progress}%')
    ext.logger.info(f'{addonid} downloaded!')
    return _filepath


def clear_directory(path: str):
    with os.scandir(path) as directory:
        for entry in directory:
            if entry.is_dir():
                rmtree(entry.path)
            else:
                os.remove(entry.path)


def unpack_addon(ext: AdminCommandExtension, addonid: str, addontag: str, filepath: str):
    if addontag == arena_l:
        addontag = track_l
    _addonpath: str = os.path.expanduser(ext.mconfig['addonpath'])
    _addonsubdir = addontag + 's'
    _target_path = os.path.join(_addonpath, _addonsubdir, addonid)
    ext.logger.info(f'Unpacking {filepath} to {_target_path}...')
    if os.path.isdir(_target_path):
        ext.logger.info(f'Removing contents of {_target_path} and replacing with new ones...')
        clear_directory(_target_path)
    else:
        os.mkdir(_target_path)
    with ZipFile(filepath) as archive:
        archive.extractall(_target_path)
    ext.logger.info(f'{addonid} successfully extracted to the addon directory.')
    return _target_path


async def update_addon(ext: AdminCommandExtension, addon: ElementTree.Element, unoutdate=True, restart=False):
    _addonid = addon.attrib['id']
    _archive_path = await download_addon(ext, _addonid, addon.attrib['file'])
    unpack_addon(ext, _addonid, addon.tag, _archive_path)
    ext.data['installed_dict'][_addonid].attrib['revision'] = addon.attrib['revision']
    ext.logger.info(f'{addon.tag.title()} "{addon.attrib["id"]}" has been updated.')
    if unoutdate:
        ext.data['updates_available'].remove(addon)
    ext.data['addonmodflag'] = True
    if restart:
        ext.ace.server_restart_clk()
        ext.data['addonmodflag'] = False


async def update_all(ext: AdminCommandExtension):
    if 'updates_available' not in ext.data:
        ext.logger.error('Cannot update: no updates available or updates are not enabled.')
        return
    _updates: Sequence[ElementTree.Element] = ext.data['updates_available']
    _update_banlist: Sequence[str] = delimiter.split(ext.mconfig['autoupdate_banlist'])
    ext.logger.info(f'Updating all addons ({len(_updates)} can be updated)...')
    for i in range(len(_updates) - 1, -1, -1):
        addon = _updates[i]
        _addonid = addon.attrib['id']
        if _addonid in _update_banlist:
            ext.logger.debug(f'Skipping frozen addon {addon.attrib["name"]} id={_addonid}')
            continue
        try:
            await update_addon(ext, addon, unoutdate=False)
            del _updates[i]
        except Exception:
            ext.logger.error(f'update_all: error occurred during "{addon.attrib["id"]}" update:\n{traceback.format_exc()}')
    ext.logger.info('Addons are updated')


async def install_addon(ext: AdminCommandExtension, addon: ElementTree.Element, restart=False) -> bool:
    async with ext.addon_installed.emit_and_handle(addon) as handle:
        _res, _args, _kw = handle()
        if not _res:
            return False
        _addonid = addon.attrib['id']
        _archive_path = await download_addon(ext, _addonid, addon.attrib['file'])
        addontype = addon.tag
        if addontype == arena_l:
            addontype = track_l
        addon_dir = unpack_addon(ext, _addonid, addontype, _archive_path)
        _xmlname = f'{addontype}.xml'
        _xml_path = os.path.join(addon_dir, _xmlname)
        if not os.path.isfile(_xml_path):
            ext.logger.error(f"Addon {_addonid} doesn't have {_xmlname}, cannot load into known addons list")
            _kw['error'] = 0
            handle(False)
            return False
        try:
            _xml = ElementTree.parse(_xml_path).getroot()
        except ElementTree.ParseError as exc:
            ext.logger.error(f'Cannot load addon data for {_addonid}: {exc}')
            _kw['error'] = 1
            handle(False)
            return False
        types_ = classifyAddon(_xml)
        if track_l in types_:
            ext.data['track'][_addonid] = _xml
        if soccer_l in types_:
            ext.data['soccer'][_addonid] = _xml
        if arena_l in types_:
            ext.data['arena'][_addonid] = _xml
        if kart_l in types_:
            ext.data['kart'][_addonid] = _xml
        ext.data['installed_addons'].append(_xml)
        ext.data['installed_dict'][_addonid] = _xml
        ext.data['addonmodflag'] = True
        ext.logger.info(f'Addon {_addonid} has been installed.')
        if restart:
            ext.ace.server_restart_clk()
            ext.data['addonmodflag'] = False
        return True


def ban_addon(ext: AdminCommandExtension, addon_id: str) -> bool:
    try:
        _install_banlist: MutableSequence[str] = delimiter.split(ext.mconfig['autoinstall_banlist'])
        if addon_id not in _install_banlist:
            ext.logger.info(f'Adding "{addon_id}" to the autoinstall ban list')
            _install_banlist.append(addon_id)
            ext.mconfig['autoinstall_banlist'] = ', '.join(_install_banlist)
            ext.save_config()
            return True
    except Exception:
        ext.logger.exception('Failed to ban addon "{}":'.format(addon_id))
    return False


def unban_addon(ext: AdminCommandExtension, addon_id: str) -> bool:
    try:
        _install_banlist: MutableSequence[str] = delimiter.split(ext.mconfig['autoinstall_banlist'])
        if addon_id in _install_banlist:
            ext.logger.info(f'Removing "{addon_id}" from the autoinstall ban list')
            _install_banlist.remove(addon_id)
            ext.mconfig['autoinstall_banlist'] = ', '.join(_install_banlist)
            ext.save_config()
            return True
    except Exception:
        ext.logger.exception('Failed to unban addon "{}":'.format(addon_id))
    return False


async def uninstall_addon(ext: AdminCommandExtension, addon: ElementTree.Element, ban=True, restart=False) -> bool:
    async with ext.addon_uninstalled.emit_and_handle(addon) as handle:
        _res, _args, _kw = handle()
        if not _res:
            return False
        if addon.tag == arena_l:
            addontag = track_l
        else:
            addontag = addon.tag
        _addonpath: str = ext.mconfig['addonpath']
        _addonsubdir = addontag + 's'
        _addonid = addon.attrib['id']
        _target_path = os.path.join(_addonpath, _addonsubdir, _addonid)
        xmlfile_name = kart_l if _addonid in ext.data[kart_l] else track_l
        if not os.path.isdir(_target_path):
            ext.logger.error(f'Cannot uninstall addon {_addonid}: directory "{_target_path}" not found.')
            handle(False)
            return False
        elif not os.path.isfile(os.path.join(_target_path, xmlfile_name)):
            ext.logger.warning(f'Addon {_addonid} does not have a {xmlfile_name}, uninstallation won\'t affect STK servers.')
            restart = False
        ext.logger.info(f'Removing directory "{_target_path}"...')
        rmtree(_target_path)
        ext.data['installed_addons'].remove(addon)
        del ext.data['installed_dict'][_addonid]
        if ban:
            ban_addon(ext, _addonid)
        ext.data['addonmodflag'] = True
        ext.logger.info(f'Addon {_addonid} has been uninstalled.')
        if restart:
            ext.ace.server_restart_clk()
            ext.data['addonmodflag'] = False
        return True


async def install_new_addons(ext: AdminCommandExtension):
    _autoinstall_banlist: Sequence[str] = delimiter.split(ext.mconfig['autoinstall_banlist'])

    def _predicate(triplet):
        _addon = triplet[0]
        if _addon.attrib['id'] in ext.data['installed_dict']:
            return False
        if float(_addon.attrib['rating']) < float(ext.mconfig['autoinstall_minrating']):
            return False
        if _addon.attrib['id'] in _autoinstall_banlist:
            ext.logger.debug(f'Skipping banned addon {_addon.attrib["id"]}')
            return False
        if _addon.tag == kart_l and not ext.mconfig.getboolean('autoinstall_karts'):
            return False
        return AddonStatus._xml_allowdeny_predicate(triplet)
    _allow, _deny = AddonStatus.allowdeny_pair(ext.mconfig['autoinstall_requirements'])
    _addons: Sequence[ElementTree.Element] = tuple(
        triplet[0] for triplet in filter(
            _predicate,
            zip(
                ext.data['addons_tree'].getroot(), repeat(_allow), repeat(_deny)
            )
        )
    )
    ext.logger.info(f'Downloading new addons ({len(_addons)} available)...')
    for addon in _addons:
        try:
            await install_addon(ext, addon)
        except Exception:
            ext.logger.error(f'Cannot install addon {addon.attrib["name"]} id={addon.attrib["id"]}:\n{traceback.format_exc()}')


async def update_all_installmore(ext: AdminCommandExtension, install_more=True):
    await update_all(ext)
    if install_more:
        await install_new_addons(ext)
    if ext.data['addonmodflag']:
        ext.ace.server_restart_clk()
        ext.data['addonmodflag'] = False
        await ext.addon_bulk_modified.emit()


async def autoupdate_task(ext: AdminCommandExtension):
    try:
        if 'stkaddons_autoupdate_task' in ext.tasks:
            ext.logger.error('Another autoupdate_task is already running! Aborting!')
            return
        ext.tasks['stkaddons_autoupdate_task'] = asyncio.current_task()
        if 'autoupdate_interval' not in ext.mconfig:
            raise KeyError('autoupdate_interval is not set in ext.mconfig')
        ext.logger.info(f'Autofetcher is enabled. Interval = {ext.mconfig["autoupdate_interval"]} seconds')
        while ext.mconfig.getboolean('autoupdate'):
            await asyncio.sleep(ext.mconfig.getfloat('autoupdate_interval'))
            await fetch(ext)
            await update_all(ext)
            if ext.mconfig.getboolean('autoinstall'):
                await install_new_addons(ext)
            ext.logger.info('Cleaning downloads directory')
            clear_directory(ext.mconfig['downloadpath'])
            if ext.data['addonmodflag']:
                ext.ace.server_restart_clk()
            ext.data['addonmodflag'] = False
    except Exception:
        ext.logger.error(traceback.format_exc())


def stkaddons_command_set(ext: AdminCommandExtension):
    async def check_available(cmd: AdminCommandExecutor):
        cmd.print('Checking...')
        asyncio.create_task(fetch(ext))
    ext.add_command(check_available, 'check-available', tuple(), tuple(), 'Check if there are any available addons to update')

    async def listaddons(cmd: AdminCommandExecutor, cpage: int = 1, flags_: str = None, not_installed=False):
        if not_installed:
            def _predicate(triplet):
                _addon = triplet[0]
                if _addon.attrib['id'] in ext.data['installed_dict']:
                    return False
                return AddonStatus._xml_allowdeny_predicate(triplet)
        else:
            _predicate = AddonStatus._xml_allowdeny_predicate
        if flags_ is not None:
            _allow, _deny = AddonStatus.allowdeny_pair(flags_)
            _addons: Sequence[ElementTree.Element] = tuple(
                triplet[0] for triplet in filter(
                    _predicate,
                    zip(
                        ext.data['addons_tree'].getroot(), repeat(_allow), repeat(_deny)
                    )
                )
            )
        else:
            _addons: ElementTree.Element = ext.data['addons_tree'].getroot()
        _len = len(_addons)
        _maxpage, _start, _end = paginate_range(_len, 10, cpage)
        cmd.print(f'Online addons (page {cpage} of {_maxpage}):')
        cmd.print('\n'.join(f'{ratingStars(float(addon.attrib["rating"]))} '
                            f'{kart_l.title() + " " if addon.tag == kart_l else empty_str}'
                            f'{heavy_check_sign if addon.attrib["id"] in ext.data["installed_dict"] else empty_str}'
                            f'{addon.attrib["id"]}={html.unescape(addon.attrib["name"])} '
                            f'by {addon.attrib["designer"]} '
                            f'(pub: {html.unescape(addon.attrib["uploader"])}): '
                            f'"{flags(addon.attrib["status"])}"' for addon in (_addons[i] for i in range(_start, _end))))
    ext.add_command(listaddons, 'listaddons', tuple(), ((int, 'page'), (str, 'addon flags'), (bool, 'not installed?')), 'Show online list of track addons')

    async def listinstalled(cmd: AdminCommandExecutor, cpage: int = 1, addon_type=None):
        if addon_type in (soccer_l, arena_l, track_l):
            _installed: ElementTree.Element = ext.data[addon_type]
        else:
            _installed: ElementTree.Element = ext.data['installed_dict']
        _len = len(_installed)
        _maxpage, _start, _end = paginate_range(_len, 10, cpage)
        items = tuple(_installed.items())[_start:_end]
        cmd.print(f'Installed addons (page {cpage} of {_maxpage})')
        cmd.print('\n'.join(f'{_id}: {_addon.attrib["name"]} by '
                            f'{_addon.attrib["designer"]}, '
                            f'v{_addon.attrib["version"]}, '
                            f'rev{_addon.attrib.get("revision", "?")}: {", ".join(classifyAddon(_addon))}'
                            '' for _id, _addon in items))
    ext.add_command(listinstalled, 'listinstalled', tuple(), ((int, 'page'), (str, 'type')), 'Shows the list of already installed addons')

    async def addoninfo(cmd: AdminCommandExecutor, addonid: str):
        if addonid in ext.data['addons_dict']:
            _addon: ElementTree.Element = ext.data['addons_dict'][addonid]
            _name = _addon.attrib.get('name', '-')
            _author = _addon.attrib.get('designer', '(Unknown author)')
            _uploader = _addon.attrib.get('uploader', '(Unknown uploader)')
            _remote_revision = _addon.attrib.get('revision', '-')
            _min_include_version = _addon.attrib.get('min-include-version', '*') or '*'
            _max_include_version = _addon.attrib.get('max-include-version', '*') or '*'
            _download_link = _addon.attrib.get('file', '(not provided)')
            _rating = float(_addon.attrib.get('rating', '0'))
            _rating_stars = ratingStars(_rating)
            if addonid in ext.data['installed_dict']:
                installed = True
                _installed = ext.data['installed_dict'][addonid]
                _classes = ', '.join(classifyAddon(_installed))
                _default_lap_count = _installed.attrib.get('default-lap-count', '-')
                _local_revision = _installed.attrib.get('revision', '-')
                _version = _installed.attrib.get('version', '-')
            else:
                installed = False
                _on_install = '(on-install)'
                _classes = _on_install
                _default_lap_count = _on_install
                _local_revision = _on_install
                _version = _on_install
        elif addonid in ext.data['installed_dict']:
            cmd.print('This addon has been removed or was never uploaded')
            _installed = ext.data['installed_dict'][addonid]
            installed = True
            _name = _installed.attrib.get('name', '-')
            _classes = ', '.join(classifyAddon(_installed))
            _default_lap_count = _installed.attrib.get('default-lap-count', '-')
            _author = _installed.attrib.get('designer', '(Unknown author)')
            _not_uploaded = '(not uploaded)'
            _uploader = _not_uploaded
            _remote_revision = _not_uploaded
            _local_revision = _installed.attrib.get('revision', '-')
            _min_include_version = '*'
            _max_include_version = _min_include_version
            _download_link = _not_uploaded
            _rating = _not_uploaded
            _rating_stars = 'xxxxxx'
            _version = _installed.attrib.get('version', '-')
        else:
            cmd.error(f'Addon "{addonid}" not found.')
            return
        cmd.print('\n'.join((f'{_rating_stars} {_name} ',
                             f'{heavy_check_sign if installed else empty_str} ',
                             f'Author(s): {_author}',
                             f'Uploader: {_uploader}',
                             f'Game mode(s): {_classes}',
                             f'Default lap count: {_default_lap_count}',
                             f'Version: {_version}',
                             f'Remote revision: {_remote_revision}',
                             f'Installed revision: {_local_revision}',
                             f'Supported version range: {_min_include_version}-{_max_include_version}',
                             f'Download link: {_download_link}')))

    async def addoninfo_tab(cmd: AdminCommandExecutor, addonid: str = '', *, argl: str):
        return tab_complete_onlineaddon(ext, addonid)
    ext.add_command(addoninfo, 'addoninfo', ((str, 'addonid'), ), tuple(), 'Show common information about addon', addoninfo_tab)

    async def updates(cmd: AdminCommandExecutor, cpage: int = 1):
        if 'updates_available' in ext.data:
            _upd: Sequence[ElementTree.Element] = ext.data['updates_available']
            _len = len(_upd)
            _maxpage, _start, _end = paginate_range(_len, 10, cpage)
            cmd.print(f'Available updates for addons (page {cpage} of {_maxpage}):')
            cmd.print('\n'.join(f'{_upd[i].attrib["id"]}: {_upd[i].attrib["name"]} by {_upd[i].attrib["designer"]}'
                                '' for i in range(_start, _end)))
    ext.add_command(updates, 'updates', tuple(), ((int, 'page'), ), 'Show list of outdated addons')

    async def downloadaddon(cmd: AdminCommandExecutor, addonid: str):
        if addonid not in ext.data['addons_dict']:
            cmd.error(f'Addon {addonid} not found.')
            return
        _addon: ElementTree.Element = ext.data['addons_dict'][addonid]
        asyncio.create_task(download_addon(cmd, addonid, _addon.attrib['file']))
        cmd.print('Starting to download addon {addonid}')

    async def downloadaddon_tab(cmd: AdminCommandExecutor, addonid: str = '', *, argl: str):
        return tab_complete_onlineaddon(ext, addonid)
    ext.add_command(downloadaddon, 'downloadaddon', ((str, 'addonid'), ), description='Download addon archive to downloads directory', atabcomplete=downloadaddon_tab)

    async def unpackaddon(cmd: AdminCommandExecutor, addonid: str):
        _archive_path = os.path.join(ext.mconfig['downloadpath'], addonid + '.zip')
        _addontype: str = ext.data['addons_dict'][addonid].tag
        if not os.path.isfile(_archive_path):
            cmd.error(f'Addon {_archive_path} is not downloaded')
            return
        try:
            unpack_addon(ext, addonid, _addontype, _archive_path)
        except Exception:
            cmd.error(traceback.format_exc())
        if ext.data['addonmodflag']:
            ext.ace.server_restart_clk()
            ext.data['addonmodflag'] = False
        cmd.print(f'{_addontype.title()} addon extracted.')

    async def unpackaddon_tab(cmd: AdminCommandExecutor, addonid: str = '', *, argl: str):
        _res = []
        with os.scandir(ext.mconfig['downloadpath']) as directory:
            _res.extend(entry.name.rpartition(dot)[0] for entry in directory if entry.name.endswith('.zip') and entry.name.startswith(addonid))
        return _res
    ext.add_command(unpackaddon, 'unpackaddon', ((str, 'addonid'), ), description='Unpack downloaded addon to the addons directory', atabcomplete=unpackaddon_tab)

    async def installaddon(cmd: AdminCommandExecutor, addonid: str):
        if addonid not in ext.data['addons_dict']:
            cmd.error(f'Couldn\'t find addon "{addonid}"')
        _addon: ElementTree.Element = ext.data['addons_dict'][addonid]
        task = asyncio.create_task(install_addon(ext, _addon, restart=True))
        ext.tasks[task.get_name()] = task

    async def installaddon_tab(cmd: AdminCommandExecutor, addonid: str = '', *, argl: str):
        def _predicate(_addon):
            if _addon.attrib['id'] in ext.data['installed_dict']:
                return False
            return _addon.attrib['id'].startswith(addonid)
        return list(
            addon.attrib['id'] for addon in filter(
                _predicate,
                ext.data['addons_tree'].getroot()
            )
        )
    ext.add_command(installaddon, 'installaddon', ((str, 'addonid'), ), description='Download and unpack an addon', atabcomplete=installaddon_tab)

    async def updateaddon(cmd: AdminCommandExecutor, addonid: str):
        _updates_available = ext.data['updates_available']
        _addon: ElementTree.Element = ext.data['addons_dict'][addonid]
        if _addon not in _updates_available:
            cmd.error(f'No available update for "{addonid}"')
            return
        task = asyncio.create_task(update_addon(ext, _addon, restart=True))
        ext.tasks[task.get_name()] = task

    async def updateaddon_tab(cmd: AdminCommandExecutor, addonid: str = '', *, argl: str):
        return list(filter(lambda name: name.startswith(addonid), (addon.attrib['id'] for addon in ext.data['updates_available'])))
    ext.add_command(updateaddon, 'updateaddon', ((str, 'addonid'), ), description='Install update for an addon', atabcomplete=updateaddon_tab)

    async def updateall(cmd: AdminCommandExecutor, _installnew=False):
        task = asyncio.create_task(update_all_installmore(ext, _installnew))
        ext.tasks[task.get_name()] = task
    ext.add_command(updateall, 'updateall', optargs=((bool, 'install new addons?'), ), description='Install all available updates (and download more addons if specified)')


async def extension_init(ext: AdminCommandExtension):
    ext.data['addonmodflag'] = False
    ext.addon_installed = AIOHandlerChain()  # (addon: Element)
    ext.addon_uninstalled = AIOHandlerChain()  # (addon: Element)
    ext.addon_updated = AIOHandlerChain()  # (addon: Element)
    # fired explicitly
    ext.addon_bulk_modified = AIOHandlerChain(cancellable=False)  # ()
    ext.mconfig_path = config_path = os.path.join(ext.ace.extpath, 'stkswrapper.conf')

    def save_config(newfile=False):
        with open(config_path, 'x' if newfile else 'w') as file:
            ext.config.write(file)

    def load_config():
        ext.config.read(config_path)
    ext.load_config = load_config
    ext.save_config = save_config
    ext.logger = ext.ace.logger.getChild('STKSWrapper')
    ext.logger.propagate = True
    ext.logger.setLevel(logging.INFO)
    config = ext.config = ConfigParser(allow_no_value=True)
    config.read_dict(defaultconf)
    ext.mconfig = config['AddonUpdater']
    if not os.path.isfile(config_path):
        save_config()
    else:
        load_config()
    if not os.path.isdir(ext.mconfig['downloadpath']):
        os.mkdir(ext.mconfig['downloadpath'])
    fetch_installed(ext)
    stkaddons_command_set(ext)
    ext.clientsession = ClientSession()
    if ext.mconfig.getboolean('autoupdate'):
        asyncio.create_task(autoupdate_task(ext))
    # asyncio.create_task(fetch(ext))


async def extension_cleanup(ext: AdminCommandExtension):
    await ext.clientsession.close()
