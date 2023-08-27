from admin_console import AdminCommandExtension, AdminCommandExecutor
# from code import InteractiveInterpreter
from functools import partial
import re


async def aexec(code, globals_=globals(), locals_=locals()):
    # Make an async function with the code and `exec` it
    if '__ex' in locals_:
        prev_ex_ = True
        prev_ex = locals_['__ex']
    else:
        prev_ex_ = False
    exec(
        f'async def __ex(): {code}',
        globals_, locals_
    )

    # Get `__ex` from local variables, call it and return the result
    _val = await locals_['__ex']()
    if prev_ex_:
        locals_['__ex'] = prev_ex
    else:
        del locals_['__ex']
    return _val


async def extension_init(ext: AdminCommandExtension):
    await_p = re.compile(r'await +(.*)')
    _locals = ext.locals = {'ext': ext, 'cmd': ext.ace, 'print': ext.ace.print}
    # interpreter = ext.interpreter = InteractiveInterpreter(ext.locals)
    # ext.locals['interpreter'] = interpreter
    # interpreter.write = ext.ace.print
    # interpreter_builtins = interpreter.locals['__builtins__']

    async def pyexec(cmd: AdminCommandExecutor, expr: str, async_: bool):
        if async_:
            await aexec(expr, _locals)
        else:
            exec(expr, _locals)
    ext.add_command(partial(pyexec, async_=False), 'exec>', ((None, 'python code'), ), description='Execute Python code')
    ext.add_command(partial(pyexec, async_=True), 'async>', ((None, 'python code'), ), description='Execute Python code as a coroutine')

    async def pyeval(cmd: AdminCommandExecutor, expr: str):
        _await_match = await_p.fullmatch(expr)
        if _await_match:
            # _code = interpreter.compile(_await_match.group(1))
            # _val = eval(_code, ext.locals)
            _val = await eval(_await_match.group(1), _locals)
        else:
            # _code = interpreter.compile(expr)
            # _val = eval(_code, ext.locals)
            _val = eval(expr, _locals)
        cmd.print(_val)
        _locals['_'] = _val
        # cmd.print(type(interpreter.locals['__builtins__']['_']))
    ext.add_command(pyeval, '>', ((None, 'python code'), ), description='Evaluate Python code')

    async def pyexec_console(cmd: AdminCommandExecutor, quitstr="quit"):
        while True:
            _line = await cmd.ainput.prompt_line("exec>>> ")
            if _line == quitstr:
                break
            _await_match = await_p.fullmatch(_line)
            if _await_match:
                await aexec(_line, _locals)
            else:
                exec(_line, _locals)
    ext.add_command(pyexec_console, 'pyexec-console', optargs=((None, 'quitstr'), ), description='Enter interactive Python console "quit" to return back')


async def extension_cleanup(ext: AdminCommandExtension):
    pass
