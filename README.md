# stkwrapper
SuperTuxKart Server written in Python with CLI interface. Supports autorestarting, extensions and automatic add-on downloading/upgrading.

Tested on STK 1.4 git version.

## Compatibility notice
Please note that older STK versions won't work properly because of [this](https://github.com/supertuxkart/stk-code/pull/4871) issue in the official STK code that is only resolved in the git version.


# Requirements
These Python packages must be present in the system. Install it with your preferred way (system package manager if you are running Linux/Unix, or pip inside a virtual environment):
```
packaging
defusedxml
emoji
git+https://github.com/NobWow/admin-console-python.git
```
Otherwise you can install all the requirements with `pip install -r requirements.txt`
-------------------------------------------------------------------------------------
# Brief tutorial
## CLI
The wrapper utilizes [admin-console-python](https://github.com/NobWow/admin-console-python) package for the interface. Therefore, to list all the commands you can either use tab completion or enter `help` command.
## Default STK configuration
`stkw_advanced` extension requires `extensions/stkdefault.xml` config file to be present.
It's a default STK server configuration file (however it is not copied to the server directory when server is created, it is a known issue)
Copy the contents from [NETWORKING.md](https://github.com/supertuxkart/stk-code/blob/master/NETWORKING.md) or use a different of your preferences.
## Automatic Add-On Updater
Firstly, make sure to configure it through `extensions/stkswrapper.conf`. Here is an example configuration:
```ini
[AddonUpdater]
online_assets_url = https://online.supertuxkart.net/downloads/xml/online_assets.xml
# Since 1.4 servers support add-on karts.
fetch_karts = True
autoupdate = True
autoupdate_interval = 21600
# These add-ons won't be updated. For example:
autoupdate_banlist = kart_corner, soccer-arena
# If this option is True, it will install new addons that meet the requirements
autoinstall = False
autoinstall_karts = True
# Addons below this rating won't be installed. Specify value between 0.0 and 5.0
autoinstall_minrating = 1.0
# Specify the requirements for the addons to be autoinstalled separated with commas.
# Prefix + means that the flag must be present, and - makes sure that it does not install the add-ons with this flag.
# Supported flags are: APPROVED ALPHA BETA RC INVISIBLE HQ DFSG FEATURED LATEST BAD_DIM
autoinstall_requirements = +APPROVED,+DFSG,-ALPHA
# These add-ons won't be installed automatically. For example:
autoinstall_banlist = kart_corner, soccer-arena
# Directory to temporarily download zip files
downloadpath = downloads
# Absolute path to the addons directory. Replace ~ with your home directory
addonpath = ~/.local/share/supertuxkart/addons
```
---
## Server creation
Make sure you have a compiled binary of SuperTuxKart and followed all the instructions in [INSTALL.md](https://github.com/supertuxkart/stk-code/blob/master/INSTALL.md) and [NETWORKING.md](https://github.com/supertuxkart/stk-code/blob/master/NETWORKING.md), except the server launching command as it is handled by the wrapper.
You must keep stk-code available, as it is required to run a binary.
Once everything STK related is ready, start the wrapper:
```
python stkserver_wrapper.py
```
Wrapper supports interactive server creation to make it easier. Dispatch this command:
```
stk-make-server
```
It will guide you through the server creation process.
Step 1: it will prompt for the server name that will be used for commands. Let's say it will be `tutorial`
```
-=STK=-: stk-make-server
Enter the server name. It will be used for further interaction with the server,
You cannot change it later
but every name should be unique.
name: tutorial
```
Step 2: working directory for the server process. Useful in some cases. Let's use the default value `./tutorial` just by pressing Enter in this case:
```
Enter the path to server's working directory.
You can change it later
Current working directory: "/home/user/stkwrapper" for relative path reference
Press return to skip and set the default value "/home/user/stkwrapper/tutorial" 
cwd: 
```
Additionally, if this directory doesn't exist, which is likely, it will ask you to create this directory. Press `y` to confirm:
```
This directory doesn't exist. Create one?
create dir "/home/user/stkwrapper/tutorial"? y
```
Step 3: server configuration file path. It will be passed as `--server-config=` argument to the server process as noted in [NETWORKING.md](https://github.com/supertuxkart/stk-code/blob/master/NETWORKING.md). Let's specify `server.xml`:
```
Directory created for server
Enter the path to configuration file. It must have the XML format (.xml).
You can change it later
Current working directory: "/home/user/stkwrapper/tutorial" for relative path reference
Press return to skip and set the default value "" 
cfgpath: server.xml
```
If the file doesn't exist, it will notice it and suggest to correct the path. You can skip this step to let the server create the configuraton file by hitting `y`. If you hit `n` it will repeat the step above. Let's skip this warning:
```
This file doesn't exist. Skip?
skip? y
```
Step 4: path to the directory that contains `data/`. It is required for every SuperTuxKart instance. If SuperTuxKart is installed in the system, the path could be `/usr/share/supertuxkart` or `/usr/local/share/supertuxkart`. If you built STK from the source and did not run `sudo make install` without deleting the sources, specify the path to the source code directory. Let's say that the supertuxkart repository is cloned to the home directory: `~/stk-code`
```
Enter the path to the "data" directory that will be used for the new server.
You can change it later
Current working directory: "/home/user/tutorial" for relative path reference
Press return to skip and set the default value "stk-code" 
TIP: it is usually either /usr/share/supertuxkart
    or in case of GIT version /path/to/stk-code
datapath: ~/stk-code
```
Step 5: Specify where the executable file is. It is either at `/usr/bin/supertuxkart` or `/usr/local/bin/supertuxkart` depending on your STK installation. In this tutorial, it is assumed that the STK has been built from the sources inside the `~/stk-code/build/` directory. So let's say that the executable is at `~/stk-code/build/bin/supertuxkart`:
```
Enter the path to supertuxkart executable file (program).
You can change it later
Current working directory: "/home/user/tutorial" for relative path reference
Press return to skip and set the default value "supertuxkart" 
exec: ~/stk-code/build/bin/supertuxkart
```
Next steps will be purely wrapper-related.
Step 6: whether or not the server is started when wrapper is started. Let's hit the `y` key so you don't have to manually start the server with `stk-start tutorial`:
```
Should server automatically start after wrapper has been launched? Hit y for yes or n for no.
You can change it later
autostart? y
```
Step 7: whether or not enable autorestart on server crash. Let's hit the `y` key to make sure that the server is back online when something happens.
```
In case the server crashes, does it require automatic restart? Enter yes or no.
You can change it later
autorestart? y
```
Step 8: forced interval-based autorestart timer. Let's leave the empty prompt and just hit Enter:
```
Is it needed to restart the server every N minutes? Leave empty string or 0 if autorestarts aren't required.
You can change it later
Note: the server will not restart if there are players at the moment
timed autorestart minutes (or empty): 
```
Step 9: how much time should the server not exceed when starting up? Let's leave the default value (120 seconds) by hitting Enter:
```
How many seconds the server has to initialize? When this timeout exceeds during server startup, the process is killed.
Current working directory: "/home/user" for relative path reference
Press return to skip and set the default value "120.0" 
startup timeout (n.n): 
```
Step 10: same as the above, but when the server is shutting down. The default value can be too high in most cases so let's specify `6.0` seconds:
```
How many seconds the server has to shutdown?When this timeout exceeds during server shutdown, the process is killed.
Current working directory: "/home/user" for relative path reference
Press return to skip and set the default value "120.0" 
shutdown timeout (n.n): 6.0
```
**Next steps are for advanced administrators**
Step 11: specify additional environment variables. Useful for ranked servers or for servers that has a separate addon setup. Let's specify `-`:
```
Advanced: which additional environment variables to pass to the process?
For example, you can specify XDG_DATA_HOME=/path/to/directory HOME=/some/directory/path
You can change it later
To clear extra argument, specify -
extra environment variables: -
```
Step 12: additional command line arguments to be passed to the process. Let's specify `-`:
```
Advanced: any additional arguments to the command line? Just leave it empty if you have no idea.
You can change it later
To clear extra argument, specify -
extra args: -
```

Final step: the server is ready to be started:
```
Server successfully created. Start it right now?
start tutorial? 
```
Hit `y` to start the server immediately or `n` to skip and start it manually later.

## Starting, restarting and stopping the server
To start the server, execute `stk-start server_name`, for example `stk-start tutorial` if you did the tutorial above.
To stop the server, execute `stk-stop server_name`. This command will make sure that there are no online players. To forcefully stop the server use `stk-stop server_name yes`
Restart command combines both of them: `stk-restart server_name`

If the wrapper is closed with `Ctrl+C` or `exit` command, it will stop all the servers forcefully **without checking if there are players online**. Be careful with that!

## Executing network console commands
You can send one line with `stk-cmd server_name line...` where `line...` is a command (spaces are allowed)
Alternatively, you can enter the network console mode with `stk-nc server_name` and send commands directly to the server, when done enter `.quit` to return to the normal command prompt.

## Undocumented
Even this short brief tutorial is quite big, so, here are the features that are undocumented:
* patterns for ignoring logs.
* configuration editing commands.
* `stk-enhance` command.
