"""
This extension adds config editing commands to the console
"""
from admin_console import AdminCommandExtension, AdminCommandExecutor
from functools import partial
from xml.etree.ElementTree import SubElement
import weakref


async def extension_init(ext: AdminCommandExtension):
    stkw_advanced = weakref.proxy(ext.ace.extensions['stkw_advanced'])
    stkserver_tab = stkw_advanced.module.stkserver_tab
    _startswith_predicate = stkw_advanced.module._startswith_predicate

    async def stk_reloadcfg(cmd: AdminCommandExecutor, servername: str):
        if servername not in stkw_advanced.server_enhancers:
            cmd.error(f'Server "{servername}" not found or not enhanced.', log=False)
            return
        enhancer = stkw_advanced.server_enhancers[servername]
        enhancer.load_serverconfig()
        cmd.print(f'Reloaded config for server "{servername}"')
    ext.add_command(stk_reloadcfg, 'stk-reloadcfg', ((str, 'servername'), ),
                    description='Read the server configuration to the enhancer.',
                    atabcomplete=stkserver_tab)

    async def stkserver_cfg_tab(cmd: AdminCommandExecutor, servername: str = "", cfgkey: str = "", *args, argl: str):
        if not servername and not argl:
            return await stkserver_tab(cmd, servername, argl=argl)
        elif servername and not argl and not cfgkey:
            return await stkserver_tab(cmd, servername, argl=argl)
        elif servername and argl and not cfgkey and servername in stkw_advanced.server_enhancers:
            enhancer = stkw_advanced.server_enhancers[servername]
            return list(element.tag for element in enhancer.servercfg.iter())
        elif servername not in stkw_advanced.server_enhancers:
            return
        enhancer = stkw_advanced.server_enhancers[servername]
        return list(element.tag for element in enhancer.servercfg.iter() if element.tag.startswith(cfgkey))

    
    async def stk_getcfg(cmd: AdminCommandExecutor, servername: str, cfgkey: str):
        if servername not in stkw_advanced.server_enhancers:
            cmd.error(f'Server "{servername}" not found or not enhanced.', log=False)
            return
        enhancer = stkw_advanced.server_enhancers[servername]
        element = enhancer.servercfg.find(cfgkey)
        if element is None:
            cmd.error(f'Element "{cfgkey}" not found.', log=False)
            return
        cfgvalue = element.attrib['value']
        cmd.print(f'{cfgkey} = {cfgvalue}')
    ext.add_command(stk_getcfg, 'stk-getcfg', ((str, 'servername'), (str, 'cfgkey')),
                    description='Shows the configuration value of specific STK server',
                    atabcomplete=stkserver_cfg_tab)

    async def stk_setcfg(cmd: AdminCommandExecutor, servername: str, cfgkey: str, value: str):
        if servername not in stkw_advanced.server_enhancers:
            cmd.error(f'Server "{servername}" not found or not enhanced.', log=False)
            return
        enhancer = stkw_advanced.server_enhancers[servername]
        element = enhancer.servercfg.find(cfgkey)
        if element is None:
            element = SubElement(enhancer.servercfg, cfgkey)
        element.attrib['value'] = value
        enhancer.save_serverconfig(later=enhancer.server.active)
        cmd.print(f'Set config value "{cfgkey}" = "{value}"')
    ext.add_command(stk_setcfg, 'stk-setcfg', ((str, 'servername'), (str, 'key'), (None, 'value')),
                    description='Sets the configuration value and schedules the server restart if server is active.',
                    atabcomplete=stkserver_cfg_tab)


async def extension_cleanup(ext: AdminCommandExtension):
    pass
