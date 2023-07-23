# girlposter

Simple reddit image (and probably gif) forwarder to telegram.

See it in action [here](https://girlposter.t.me) and as a [bot](https://girlposterbot.t.me)


## install 

```
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r requirements.txt
cp config.toml-example config.toml && vim config.toml  # where to forward and what subreddits to forward
cp telethon_config.toml-example telethon_config.toml && vim telethon_config.toml  # API id+hash and bot token here
```

## run

```
source venv/bin/activate
python3 girlposter.py
```

systemd service may be created to start at system startup
