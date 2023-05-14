#!/usr/bin/python3

import io, PIL, PIL.Image, PIL.ImageDraw, PIL.ImageFont
import telethon
from datetime import datetime, timezone
import subprocess
import xmltodict, html
import asyncio, aiohttp, kaioretry
import pytomlpp
import rich, rich.traceback, rich.console


console  = rich.console.Console()
excprint = lambda: console.print_exception(show_locals=True)
rich.traceback.install(show_locals=True)

telethon_config = pytomlpp.load("telethon_config.toml")
api_id    = telethon_config['api_id']
api_hash  = telethon_config['api_hash']
bot_token = telethon_config['bot_token']

config = pytomlpp.load("config.toml")
channel_id = config['channel_id']  # where forwarded messages go to
log_id     = config['log_id']      # for debugging

reddit_url = f"https://reddit.com/r/{'+'.join(config['subs'])}/new.rss"
user_agent = 'Mozilla/5.0 (X11; Linux x86_64; rv:101.0) Gecko/20100101 Firefox/101.0'

font_url             = "https://fontcdn.ctw.re/fonts/JetBrains_Mono/JetBrainsMono-Regular.ttf"
font_small, font_big = None, None
font_size_small      = 12
font_size_big        = 20

delay = 50  # delay in seconds between consequent attempts to read reddit feed

bot = telethon.TelegramClient('girlposter', api_id, api_hash).start(bot_token=bot_token)


async def init_font(session):
    global font_small
    global font_big
    async with session.get(font_url) as resp:
        font_data = await resp.read()
    with io.BytesIO(font_data) as font_file:
        font_small = PIL.ImageFont.truetype(font_file, font_size_small)
    with io.BytesIO(font_data) as font_file:
        font_big   = PIL.ImageFont.truetype(font_file, font_size_big)


def reverse_unicode(s):
    q = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and s[i+1] in 'uU':
            if s[i+1] == 'u':
                # 4 char
                val = int('0x' + s[i+2:i+6], 16)
            elif s[i+1] == 'U':
                val = int('0x' + s[i+2:i+10], 16)
            q.append(chr(val))
        else:
            q.append(s[i])
        i += 1
    return ''.join(q)


escape_chars = r'\[]()|_~*`'
backslash = '\\'
def escape(s):
    res = []
    for c in s:
        if c in escape_chars:
            res.append(backslash)
        res.append(c)
    return ''.join(res)


def one_of_in(arr, s):
    for x in arr:
        if x in s:
            return True
    return False


def image_to_png(im):
    f = io.BytesIO()
    im.save(f, format='PNG')
    f.seek(0)
    return f


async def overlay_image(session, url, desc):
    async with session.get(url) as resp:
        data = await resp.read()
    image = PIL.Image.open(io.BytesIO(data))

    w, h = image.size
    LO, HI = 800, 2560
    if not (LO <= w <= HI and LO <= h <= HI):
        # resize to fit LOxLO <-> HIxHI
        swap = w < h
        if swap:
            w, h = h, w
        mul = HI if w > HI else LO
        w, h = mul, h * mul // w
        if swap:
            w, h = h, w
        image = image.resize((w,h), resample=PIL.Image.Resampling.BICUBIC)

    use_big_font = w > 1200 or h > 1200
    font = font_big if use_big_font else font_small
    offset = 2 if use_big_font else 1

    # burn 'desc' onto top right corner
    draw = PIL.ImageDraw.Draw(image)
    for dx in range(-offset, offset+1, 1):
        for dy in range(-offset, offset+1, 1):
            draw.text((w+dx-4,dy), desc, fill="white", anchor="ra", font=font)
    draw.text((w-4,0), desc, fill="black", anchor="ra", font=font)

    return image_to_png(image)


# TODO rewrite
async def overlay_vid(session, image, desc):
    image_file = '/tmp/yukiposter' + str(datetime.now(timezone.utc).replace(tzinfo=timezone.utc).timestamp()) + image[image.rfind('/')+1:]
    media = image_file + '.mp4'
    descf = image_file + "2"
    with open(descf, "w") as f:
        f.write(desc)
    subprocess.run(f'curl {image} > {image_file}', shell=True)
    cmd = ['ffmpeg', '-v', 'info',
        '-i', image_file,
        '-vf', f'scale=w=max(600\,trunc(iw/2)*2):h=-2,drawtext=font=\'Cascadia Code PL\':x=2:y=0:alpha=0.7:expansion=none:fontsize=12:box=1:boxborderw=2:boxcolor=\'white\':fontcolor=\'black\':textfile=\'{descf}\'',
        '-preset', 'fast',
        '-b:v', '600k',
        '-f', 'mp4', media]
    subprocess.Popen(cmd).wait()
    with open(media, "rb") as f:
        image = f.read()
    return media


async def overlay_text(session, url, desc):
    if not url.endswith("gif"):
        return await overlay_image(session, url, desc)
    else:
        return await overlay_vid(session, url, desc)


@kaioretry.aioretry(tries=5, delay=1, backoff=2)
async def send_image_retry(image, caption):
    await bot.send_file(channel_id, image, caption=caption, supports_streaming=False)


async def send_image(session, image, caption, desc):
    try:
        await send_image_retry(
            await overlay_text(session, image, desc),
            caption=escape(caption)
        )
    except Exception as err:
        await log_tg(f'sendimage\n{image}\n{caption}\n{desc}\n{err}')
        excprint()



# TODO add galleries support
async def get_reddit_feed(session):
    try:
        async with session.get(reddit_url, headers={'User-Agent': user_agent}) as resp:
            xml = await resp.text()
    except:
        await log_tg(f'get_reddit_feed\nlink: `{link_rss}`\ndetails: `{err}`')
        return

    dic = xmltodict.parse(xml)
    items = dic['feed']['entry']
    items_good = {}
    for item in items:
        contents = html.unescape(item['content'])['#text']

        # find link between " and ">[link] (find right then left...)
        ri = contents.find('">[link]')
        if ri == -1:
            continue
        le = contents[:ri].rfind('"')
        medialink = contents[le+1:ri]

        _id = item['id'][3:]
        items_good[_id] = {
            'title': reverse_unicode(item["title"]),
            'link': f'redd.it/{_id}',
            'sub': item['category']['@label'],
            'media': medialink
        }
    items_sorted = sorted(items_good.items(), key=lambda a: (len(a[0]), a[0]))

    return items_sorted


async def post_reddit(session):
    reddit = await get_reddit_feed(session)

    if not reddit:
        await log_tg('Could not get reddit feed!')
        return

    try:
        with open('girls.txt', 'r') as inp:
            last_reddit = inp.read().strip()
    except:
        last_reddit = ''

    with open('girls.txt', 'w') as out:
        out.write(reddit[-1][0])

    tasks = []
    for (k, v) in reddit:
        if (len(k), k) <= (len(last_reddit), last_reddit):
            continue

        title, link, sub, media = v['title'], v['link'], v['sub'], v['media']
        watermark = f'{link} in {sub} -> t.me/girlposter'

        if one_of_in(["i.redd.it", "i.imgur.com", "i.imgflip.com"], media):
            task = send_image(session, media, title, watermark)
        else:
            task = log_tg(f'Not supported\n{k} {media}')
        tasks.append(asyncio.create_task(task))

    if tasks:
        await asyncio.wait(tasks)


# TODO rewrite
async def log_tg(s):
    cutpt = 4000
    if not len(str(s)):
        1 // 0
    msg = f'{datetime.now(timezone.utc).replace(tzinfo=timezone.utc).timestamp()}\n{s}'.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('|', '\\|')
    for i in range(0, len(msg), cutpt):
        await bot.send_message(log_id, msg[i:i+cutpt])


async def main():
    async with aiohttp.ClientSession() as session:
        await init_font(session)
        while True:
            await post_reddit(session)
            await asyncio.sleep(delay)


with bot:
    bot.loop.run_until_complete(main())

