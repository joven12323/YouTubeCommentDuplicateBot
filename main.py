import requests
import sqlite3
import os
import asyncio
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import ContextTypes

# Налаштування
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not YOUTUBE_API_KEY:
    raise ValueError("YOUTUBE_API_KEY не встановлено")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не встановлено")

# Ініціалізація бази даних
conn = sqlite3.connect("comments.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS videos (
        video_id TEXT,
        chat_id TEXT
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        video_id TEXT,
        comment_text TEXT,
        comment_id TEXT,
        reported INTEGER DEFAULT 0
    )
""")
conn.commit()

# Отримання коментарів із YouTube
def get_video_comments(video_id):
    try:
        url = f"https://www.googleapis.com/youtube/v3/commentThreads?part=snippet&videoId={video_id}&key={YOUTUBE_API_KEY}&maxResults=100"
        response = requests.get(url).json()
        comments = []
        if "items" in response:
            for item in response["items"]:
                comment = item["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
                comment_id = item["snippet"]["topLevelComment"]["id"]
                comments.append((comment, comment_id))
        return comments
    except Exception as e:
        print(f"Помилка YouTube API: {e}")
        return []

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привіт! Я бот для відстеження дублів коментарів під YouTube-відео.\n"
        "Використовуй /track <video_id>, наприклад: /track ixqPzkuY_4U"
    )

# Команда /track
async def track_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ID відео! Наприклад: /track ixqPzkuY_4U")
        return
    video_id = context.args[0]
    chat_id = str(update.message.chat_id)

    # Перевірка, чи відео вже відстежується
    cursor.execute("SELECT * FROM videos WHERE video_id = ? AND chat_id = ?", (video_id, chat_id))
    if cursor.fetchone():
        await update.message.reply_text("Це відео вже відстежується!")
        return

    # Додаємо відео до відстеження
    cursor.execute("INSERT INTO videos (video_id, chat_id) VALUES (?, ?)", (video_id, chat_id))
    conn.commit()
    await update.message.reply_text(f"Відео {video_id} додано до відстеження! Перевірятиму дублі коментарів кожні 2 хвилини.")

# Команда /list
async def list_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.message.chat_id)
    cursor.execute("SELECT video_id FROM videos WHERE chat_id = ?", (chat_id,))
    videos = cursor.fetchall()
    if not videos:
        await update.message.reply_text("Ви не відстежуєте жодного відео.")
        return
    video_list = "\n".join([video[0] for video in videos])
    await update.message.reply_text(f"Відстежувані відео:\n{video_list}")

# Команда /untrack
async def untrack_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Вкажи ID відео! Наприклад: /untrack ixqPzkuY_4U")
        return
    video_id = context.args[0]
    chat_id = str(update.message.chat_id)
    
    cursor.execute("SELECT * FROM videos WHERE video_id = ? AND chat_id = ?", (video_id, chat_id))
    if not cursor.fetchone():
        await update.message.reply_text("Це відео не відстежується!")
        return
    
    cursor.execute("DELETE FROM videos WHERE video_id = ? AND chat_id = ?", (video_id, chat_id))
    cursor.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))
    conn.commit()
    await update.message.reply_text(f"Відео {video_id} видалено з відстеження.")

# Перевірка дублів коментарів
async def check_duplicates():
    cursor.execute("SELECT video_id, chat_id FROM videos")
    for video_id, chat_id in cursor.fetchall():
        # Отримуємо коментарі
        comments = get_video_comments(video_id)
        if not comments:
            continue

        # Додаємо нові коментарі до бази
        for comment_text, comment_id in comments:
            cursor.execute("SELECT * FROM comments WHERE comment_id = ?", (comment_id,))
            if not cursor.fetchone():
                cursor.execute("INSERT INTO comments (video_id, comment_text, comment_id) VALUES (?, ?, ?)",
                               (video_id, comment_text, comment_id))
        conn.commit()

        # Шукаємо дублі в межах одного відео
        cursor.execute("""
            SELECT comment_text, COUNT(*) as count
            FROM comments
            WHERE video_id = ? AND reported = 0
            GROUP BY comment_text
            HAVING count > 1
        """, (video_id,))
        duplicates = cursor.fetchall()

        # Надсилаємо повідомлення про дублі
        for comment_text, count in duplicates:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            await application.bot.send_message(
                chat_id=chat_id,
                text=f"Дубль знайдено\n{video_url}\n\nКоментар: {comment_text}\n(зустрічається {count} разів)"
            )
            # Помічаємо коментарі як повідомлені
            cursor.execute("UPDATE comments SET reported = 1 WHERE video_id = ? AND comment_text = ?",
                           (video_id, comment_text))
        conn.commit()

# Налаштування бота
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("track", track_video))
application.add_handler(CommandHandler("list", list_videos))
application.add_handler(CommandHandler("untrack", untrack_video))

# Періодична перевірка (кожні 2 хвилини)
scheduler = AsyncIOScheduler()
scheduler.add_job(check_duplicates, "interval", minutes=2)

# Асинхронна функція для запуску
async def main():
    scheduler.start()
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await asyncio.Event().wait()

# Запуск бота
if __name__ == "__main__":
    print("Бот запускається...")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот зупинений")