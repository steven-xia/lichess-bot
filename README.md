# lichess-bot
A bridge between [Lichess API](https://lichess.org/api#tag/Chess-Bot) and bots.


## How to Install
NOTE: Only Python 3 is supported!

### Mac/Linux:
- Download the repository into lichess-bot directory.
- Navigate to the directory in the terminal (eg. `cd lichess-bot`).
- Install virtualenv: `pip install virtualenv`
- Setup virtualenv:
```
virtualenv .venv -p python3  # if this fails you probably need to add Python3 to your PATH
source .venv/bin/activate
pip install -r requirements.txt
```
- Copy `config.yml.default` to `config.yml`
- Edit `config.yml` as necessary (Use `#` to disable certain ones.).

### Windows:
- Here is a video on how to install the bot: (https://youtu.be/w-aJFk00POQ). Or you may proceed to the next steps.
- If you don't have Python, you may download it here: (https://www.python.org/downloads/). When installing it, enable "add Python to PATH", then go to custom installation (this may be not necessary, but on some computers it won't work otherwise) and enable all options (especially "install for all users"), except the last . It's better to install Python in a path without spaces, like "C:\Python\".
- To type commands it's better to use PowerShell. Go to Start menu and type "PowerShell" (you may use cmd too, but sometimes it may not work).
- You may need to upgrade pip. Execute "python -m pip install --upgrade pip" in PowerShell.
- Download the repository into lichess-bot directory.
- Navigate to the directory in PowerShell: `cd [folder's adress]` (eg `cd C:\chess\lichess-bot`).
- Install virtualenv: `pip install virtualenv`.
- Setup virtualenv:
```
virtualenv .venv -p python  (if this fails you probably need to add Python to your PATH)
./.venv/Scripts/activate  (.\.venv\Scripts\activate should work in cmd in administator mode) (This may not work on Windows, and in this case you need to execute "Set-ExecutionPolicy RemoteSigned" first and choose "Y" there [you may need to run Powershell as administrator]. After you executed the script, change execution policy back with "Set-ExecutionPolicy Restricted" and pressing "Y")
pip install -r requirements.txt
```
- Copy `config.yml.default` to `config.yml`
- Edit `config.yml` as necessary (Use `#` to disable certain ones.).


## Lichess OAuth
NOTE: If you have previously played games on an existing account, you will not be able to use it as a bot account.
- Create an account for your bot on [lichess.org](https://lichess.org/signup)
- Once your account has been created and you are logged in, [create a personal OAuth2 token](https://lichess.org/account/oauth/token/create) with the "Play as a bot" selected and add a description
- A `token` (e.g. `Xb0ddNrLabc0lGK2`) will be displayed. Store this in `config.yml` as the `token` field.
NOTE: You won't see this token again on Lichess.


## Setup Engine
- Note down where you engine executable is (suggested in the `engines` directory).
- In `config.yml`, enter the folder your engine is in under `engine.dir`.
- In `config.yml`, enter the name of your engine's binary under `engine.name`. In Windows you may need to type a name with ".exe", like "lczero.exe"


## Lichess Upgrade to Bot Account
**WARNING** This is irreversible. [Read more about upgrading to bot account](https://lichess.org/api#operation/botAccountUpgrade).
- Run `python lichess-bot.py -u`


## To Quit
- Press `Ctrl + C`
- It may take some time to quit.


## Tips & Tricks
- You can specify a different config file with the `--config` argument.
- Here's an example systemd service definition:
```
[Unit]
Description=lichess-bot
After=network-online.target
Wants=network-online.target

[Service]
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 /home/thibault/lichess-bot/lichess-bot.py
WorkingDirectory=/home/thibault/lichess-bot/
User=thibault
Group=thibault
Restart=always

[Install]
WantedBy=multi-user.target
```

# Acknowledgements
Thanks to the Lichess team, especially T. Alexander Lystad and Thibault Duplessis for working with the LeelaChessZero
team to get this API up. Thanks to the Niklas Fiekas and his [python-chess](https://github.com/niklasf/python-chess) code which allows engine communication seamlessly.


# License
lichess-bot is licensed under the AGPLv3 (or any later version at your option). Check out LICENSE.txt for the full text.
