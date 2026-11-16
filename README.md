# neon_bot

A feature-rich WhatsApp bot built with the [neonize](https://github.com/Pah-0/neonize) library, designed to provide media downloading, group management, image processing, and fun utilities.

## Features

### 📥 Media Downloaders
- **Auto Downloader**: Send a link from YouTube, Instagram, TikTok, Twitter/X, Pinterest or any other [yt-dlp](https://github.com/yt-dlp/yt-dlp) supported site and the bot downloads and uploads it (enable per chat with `.ytdl_enable`). Spotify track links are fetched from YouTube Music.
  - Add `#no_ytdl` to a message to skip it, or `#yt-dlp-only` to force yt-dlp.
  - Trim videos during download using bracketed arguments (e.g., `[00:10-00:20]`).
- **TikTok**: Download TikTok videos with `.tikmate`.

### 🎞 Media Tools
- **Convert**: Video to audio, media to document, and documents back to media (add `(asm)` to a document's caption).
- **Compress**: Compress videos.
- **Inspect**: Get a video's FPS or full mediainfo.
- **Repair**: Make some unplayable videos playable.
- **Screenshot**: Generate screenshots of websites from a URL, or long screenshots of a YouTube or X post's comments.
- **Quote**: Turn a replied message into a quote sticker (or picture) with the sender's profile picture, member tag, emoji, media and the message it replied to.
- **Objection!**: Render a stretch of chat as an Ace Attorney style courtroom video.
- **TL;DR**: Summarise (`.tldr`), rewrite in simple words (`.eli5`) or say the way Genshin's Razor would (`.razor`) a replied message or image, through a Modal endpoint.

### 🎨 Sticker & Image Tools
- **Sticker Creation**: Convert images, videos, and GIFs into stickers.
- **Sticker to Image**: Convert stickers back into images or GIFs.
- **Sticker Kanging**: Rename sticker metadata to your own pack name.
- **Image Upscaling**: Enhance image quality using Real-ESRGAN through a Modal endpoint (single images or whole albums), or locally with the optional local upscaler.

### 👥 Group Management
- **Greetings**: Custom welcome and goodbye messages for group members.
- **Rules**: Set group rules and send them to members in PM.
- **Roles**: Create custom roles that members can join, then tag the whole role at once.
- **Mass Tagging**: Tag everyone (`@all`/`@everyone`), admins (`@admins`/`@mods`), owners (`@owners`) or sudoers (`@sudoers`) in a message.
- **Filters**: Automatic responses for specific keywords.
- **Notes**: Save and retrieve frequently used text or media (`.get name` or `#name`).
- **Message Ranking**: Track message statistics as leaderboard cards, posted every Sunday, with weekly leaderboards or monthly ones that reset in the first week of each month (🥇, 🥈, 🥉).
- **Moderation**: Pin/unpin and purge messages, auto-delete messages from muted users, and auto-delete mass tags.
- **Undelete**: Retrieve deleted messages in a group.

### 🎉 Fun & Entertainment
- **Random Media**: Random cat/dog pictures and videos, and short looping videos from Coub.
- **GIFs, Stickers & Memes**: Search for GIFs and stickers, or fetch random memes from Reddit.
- **Chatbot**: Chat with the bot in PM (requires `CHATBOT_DB`).

### 🎮 Games
- **Werewolf**: Play a game of Werewolf with your group.
- **Genshin Codes**: Get the current Genshin Impact redeem codes, and have new ones posted (and pinned for a day) in chats automatically.
- **Genshin Farm Schedule**: See which domains are open each day and the characters and weapons they cover (data from Project Amber), optionally posted and pinned daily.
- **Hoyo Search**: Look up characters, weapons and sets from Genshin Impact, Honkai: Star Rail and Zenless Zone Zero, with talent scaling at any level and constellations (data from Project Amber, Project Yatta and Hakushin).
- **Hoyo Profiles**: Genshin, Star Rail and ZZZ player profile cards and character build cards (stats, weapon, artifacts/relics/discs with crit value and roll counts, Akasha and Inter-Knot ranks), from Enka.Network.

### 🔎 Lookups
None of these need an API key.
- **Anime**: Anime info and airing schedules from AniList.
- **Manga & Light Novels**: Manga, manhwa, manhua and light novels from AniList.
- **Web Novels**: Chinese, Korean and Japanese web and light novels from MangaUpdates, and Wattpad stories.
- **Films & TV**: Films and series with IMDb ratings, cast and plot, from Cinemeta.
- **Fanfics**: AO3 works.

### 🛠 Utilities
- **AFK**: Set an AFK status; messages that tag or reply to you are forwarded to your DM.
- **Reminders**: Get tagged on a replied message at a specified time.
- **RSS Feeds**: Subscribe to RSS feeds and receive updates in your chats.
- **URL Sanitization**: Strip tracking parameters from links.
- **Auto Translation**: Messages that aren't in a chat's set language get a translated reply, kept up to date when the message is edited (language detected locally with py3langid, translated with Google Translate).
- **Random Choice**: Let the bot pick from a list of options.
- **Message Store**: Messages are stored locally (for undelete, objection, etc.) and cleaned up automatically after `MSG_STORE_STALE` seconds.

## Commands

The default command prefix is `.`; change it with `CMD_PREFIX`. Use `.cmds` in chat for the built-in help; it points to `.games`, `.lookup`, `.tools`, `.fun`, `.roles` and `.manage`, which list the commands below. Most commands reply with their full help when sent with `-h` or without arguments.

### General
- `.start`: Say hi.
- `.ping`: Check bot latency and uptime.
- `.afk [reason]`: Mark yourself as AFK.
- `.cmds`: List the main commands and the other lists.
- `.games` / `.lookup` / `.tools` / `.fun` / `.roles` / `.manage`: List the game, lookup, tool, fun, role and management commands.

### Games
- `.werewolf [-j/-l/-s/--start/--mode <mode>] [-r]`: Join, leave, check, start or pick the mode of a Werewolf game; `-r` deletes messages from dead players and non-players while it runs. In game: `.lynch`, `.id` (detective) and `.shoot` (gunner).
- `.gicodes [-add/-rm <chat> | -get] [-pin/-unpin <chat>]`: Get the current Genshin Impact redeem codes; owner, sudo and group admins pick chats where new codes are posted automatically, and `-pin` pins them there for a day.
- `.farm [day] [city] [asia|eu|na]`: See which Genshin talent and weapon domains are open today (or on a given day) and who they're for; `-add <chat> [region]` posts it to a chat at each daily reset, and `-pin <chat>` pins that post for a day.
- `.search <name> [-gi|-hsr|-zzz] [-char|-weapon|-set] [-t] [-c] [-full] [-lv <n>]`: Look up a Genshin Impact, Honkai: Star Rail or Zenless Zone Zero character, weapon or set; `-t` adds talents/traces/skills with their scaling at level `-lv`, `-c` constellations/eidolons/mindscapes, and `-full` both.
- `.gi`, `.hsr`, `.zzz` `[uid] [character] [-c] [-2x] [-hide]`: A player's profile card (Genshin: Spiral Abyss, Imaginarium Theater and Stygian Onslaught; Star Rail: Trailblaze and Equilibrium levels and collection; ZZZ: Shiyu Defense, Deadly Assault and Battle Tower medals) with their showcase. A character's number or name after the UID sends its build card, and `-c` sends a poll to tick several. `-2x` draws the cards at twice the resolution, `-set <uid>` saves your UID for that game, and `-hide` hides it on the cards. Send `.gi -h` (or `.hsr -h`, `.zzz -h`) for the full help.

### Lookup
Each sends a poll when several titles match.
- `.anime [query] [-m]` / `.airing [query]`: Anime info / airing schedule from AniList; `-m` takes a MyAnimeList id.
- `.manga <title|id> [-ln] [-m]`: Manga, manhwa and manhua from AniList; `-ln` for light novels, `-m` for a MyAnimeList id.
- `.novel <title> [-wp]`: Web and light novels from MangaUpdates; `-wp` for Wattpad stories.
- `.imdb <title|tt id> [-movie|-tv]`: Films and series with IMDb ratings, from Cinemeta.
- `.fic <title|work id|link>`: An AO3 work.

### Media & Tools
- `.sticker`: Convert replied media to a sticker.
- `.stick2img`: Convert a sticker back to an image/GIF.
- `.kang`: Kang a replied sticker.
- `.upscale` / `.batch_upscale`: Upscale a replied image / album.
- `.mp3`: Convert a replied video to audio.
- `.doc`: Convert replied media to a document.
- `.compress`: Compress a replied video.
- `.fps` / `.mediainfo`: Get the FPS / mediainfo of a replied video.
- `.sanitize_video`: Make a replied video playable.
- `.tikmate [url]`: Download a TikTok video.
- `.screenshot [url]`: Take a screenshot of a website.
- `.screenshot_comments [url]`: Take long screenshots of an X or YouTube post's comments.
- `.quote [-img]`: Turn a replied message into a quote sticker, or a picture with `-img`.
- `.insession` / `.renderobj`: Mark the start of an objection session / render it up to the replied message as an Ace Attorney video.
- `.sanitize [url]`: Sanitize a link or replied message.
- `.autotranslate [language | off] [-i languages]`: Translate messages that aren't in the chat's language, optionally leaving some languages alone (e.g. `-i pcm`). Owner, sudo and admins in groups.
- `.random [options]`: Pick a random choice.
- `.repeat`: Repeat a replied message.

### Notes, Filters & Reminders
- `.save [name]` / `.get [name]` / `.del_note [name]`: Save, get, or delete a note. `#name` also gets a note and `#notes` lists them.
- `.filter [word]` / `.filters` / `.del_filter [word]`: Create, list, or delete keyword filters.
- `.remindme [time]` / `.all_reminders` / `.del_reminder`: Set, list, or delete reminders.

### Group Management
- `.greetings [on/off]`: Enable or disable welcome/goodbye messages.
- `.setwelcome` / `.getwelcome` / `.unsetwelcome`: Set, view (raw, before formatting), or remove a custom welcome message.
- `.setrules` / `.delrules` / `.rules`: Set, delete, or view group rules.
- `.roles [-l | role_name]`: List role commands, list roles, or show a role.
- `.new_role` / `.edit_role` / `.del_role`: Create, edit, or delete a role. Roles can have a description (`-d`) and be closed (`-c`), so members are added by the creator or an admin instead of joining.
- `.transfer_role`: Make another member the role's creator.
- `.join_role` / `.exit_role`: Join or leave a role.
- `.my_roles`: List the roles you're in.
- `.role_tags <role> [on|off]`: Stop or resume getting tagged by a role without leaving it.
- `.add2role` / `.rm_from_role`: Add or remove users from a role.
- `@role_name` (or `@role_names`) in a group message tags the role's members; when the message replies to another, the tag says who sent it; each role can be tagged once a minute, and users can create up to 10 roles per group (admins, owner and sudo are exempt from both).
- `.msg_ranking [-f] [-text]`: Get the group message leaderboard card; `-f` for everyone, `-text` as text.
- `.amr_enable [weekly/monthly]` / `.amr_disable`: Toggle automatic message ranking.
- `.pin` / `.unpin`: Pin or unpin a replied message.
- `.purge`: Mass delete messages.
- `.mute [-a/-rm/-c] [-role <role> | -except <role>]`: Manage users whose messages are auto-deleted; `-role` mutes a role's members and `-except` mutes everyone outside a role. Admins, owner and sudo are never muted.
- `.enable_atd` / `.disable_atd`: Toggle auto-deleting mass tags.
- `.ytdl_enable` / `.ytdl_disable`: Toggle the auto downloader in a chat.
- `.enable` / `.disable`: Enable or disable bot replies in a group.
- `.undel`: Retrieve deleted messages.
- `.del`: Delete a replied bot message.
- `.gc_info`: Get the group's owner, creation date, id and admins.

### Fun
- `.cat` / `.dog` / `.coub`: Random cat, dog, or Coub media.
- `.gif [query]` / `.gsticker [query]`: Random GIF or sticker from search results.
- `.meme [subreddit]`: Random meme.

### Owner & Dev
- `.sudo [-a/-rm/-c]`: Manage sudo users.
- `.ban` / `.unban`: Ban or unban a user from using the bot.
- `.pause [on/off]`: Make the bot ignore non-owners.
- `.rss`: Manage RSS feed subscriptions.
- `.call [link]` / `.endcall`: Play a replied audio or a link into a voice call in the chat (for testing; needs `ENABLE_CALLS`) / hang up.
- `.update` / `.restart`: Update or restart the bot.
- `.update -pr <number>`: Switch to a pull request's code to test it. `.update` goes back to the main code, and so does the bot by itself if it crashes on the pull request (when started with `run.sh`).
- `.logs`: Get the bot logs.
- `.eval [code]` / `.bash [cmd]`: Run Python code or shell commands.

## Configuration

The bot can be configured via environment variables or a `.env` file (see `.env.sample`). Don't use quotes around values, and separate multiple values with spaces.

### Required

| Variable | Description |
| --- | --- |
| `PH_NUMBER` | Your WhatsApp phone number (with country code, without `+`). |
| `OWNER` | Phone number(s) of the bot owner(s) (with country code, without `+`). |

### Optional

| Variable | Default | Description |
| --- | --- | --- |
| `ALLOWED_CHATS` | | IDs of chats where the bot is allowed to operate. |
| `ALWAYS_DEPLOY_LATEST` | `False` | Pull the latest code from upstream on every start. |
| `ALWAYS_UPDATE_PY_REQ` | `False` | Reinstall Python requirements on every update. |
| `BACKUP_WA_DB` | | Postgres URL to periodically back up `WA_DB` to (requires `WA_DB` to be a Postgres URL and the Postgres client tools). |
| `BANNED_USERS` | | Users the bot should ignore. |
| `BLOCK_NSFW` | `True` | Enable/disable NSFW filtering. |
| `CMD_PREFIX` | `.` | Command prefix. |
| `CHATBOT_DB` | | MongoDB URL for the chatbot; the chatbot is disabled if unset. |
| `DATABASE_URL` | | MongoDB connection string. Without it, data is kept in a local SQLite file, `.local.db`. Data from older versions is moved over on first start; the old MongoDB documents are kept, and old `.local_*.pkl` files are renamed to `.pkl.bak`. |
| `DBNAME` | `Neon` | MongoDB database name. |
| `DB_ID` | `0000` | Database document ID, used when `PH_NUMBER` is not set. |
| `DEBUG` | `False` | Enable debug logging. |
| `DEV` | | Phone numbers of developers (allowed to use dev tools). |
| `DISABLE_CIP` | `False` | Disable CPU-intensive processes (local image upscaling). Local upscaling is also off when its optional requirements aren't installed. |
| `ENABLE_CALLS` | `False` | Enable voice calls, for `.call`. |
| `IGNORE_PM` | `True` | Ignore private messages. Has no effect if `ALLOWED_CHATS` is set. |
| `KLIPY_API_KEY` | | API key for GIF and meme search. |
| `LOG_GROUP` | | Chat ID to send logs and errors to (`id` or `id:server`). |
| `MODAL_TLDR_API` | | URL of the Modal app in `modal/modal_tldr.py`, for `.tldr`, `.eli5` and `.razor`. Deploy it with `modal deploy modal/modal_tldr.py` after creating a `tldr-auth-token` secret with an `API_TOKEN`. |
| `MODAL_TLDR_TOKEN` | | The `API_TOKEN` of `MODAL_TLDR_API`. |
| `MODAL_UPSCALE_API` | | URL of a Modal upscaling endpoint. |
| `MODAL_UPSCALE_TOKEN` | | Token for `MODAL_UPSCALE_API`. |
| `MSG_STORE` | `sqlite+aiosqlite:///msg_store.db` | SQLAlchemy async URL of the local message store. |
| `MSG_STORE_STALE` | `2592000` | Seconds before stored messages are deleted (30 days); `0` disables cleanup. |
| `NO_GPU` | `False` | Don't use the GPU for upscaling even if one is available. |
| `RSS_CHAT` | | Default chat ID(s) to send RSS feed updates to. |
| `RSS_DELAY` | `60` | Seconds between RSS feed checks. |
| `TELEGRAPH_API` | `https://api.telegra.ph` | Telegraph API URL. |
| `UPSTREAM_BRANCH` | `main` | Branch to update from. |
| `UPSTREAM_REPO` | `https://github.com/Pah-0/neon_bot` | Repository to update from. |
| `WA_DB` | `db.sqlite3` | Path or URL of the WhatsApp session database. |
| `WA_DB_BACKUP_INTERVAL` | `43200` | Seconds between `WA_DB` backups. |

## Installation

### Standard Deployment

1.  Clone the repository:
    ```bash
    git clone https://github.com/Pah-0/neon_bot.git
    cd neon_bot
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
    For local image upscaling without Modal, also install the optional upscaler (Real-ESRGAN with torch, a large download):
    ```bash
    pip install -r requirements-upscale.txt
    ```
3.  Configure your environment variables in a `.env` file.
4.  Run the bot:
    ```bash
    bash run.sh
    ```

### Docker Deployment

1.  Build the Docker image:
    ```bash
    docker build -t neon_bot .
    ```
    Add `--build-arg LOCAL_UPSCALE=true` to include the local upscaler.
2.  Run the container:
    ```bash
    docker run -it --env-file .env neon_bot
    ```

## License

This project is licensed under the GNU General Public License v3. See the `LICENSE` file for details.