import telebot
import os
import re
import requests
import schedule
import time
from threading import Thread
import functools
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
from telebot import types

from urllib.parse import urlparse, urljoin

API_TOKEN = ''

bot = telebot.TeleBot(API_TOKEN)

user_collection = {}


TIME_VAR = 60 * 2

def get_start_text():
    return ("Привет! Я бот для репостинга с joyreactor. " +
            "Добавь меня в чат и дай админку для полноты работы функционала. " +
            "Также ты можешь прописать какие теги нужно заблокировать и какой минимальный рейтинг у арта должен быть.\n" +
            "Команды:\n" +
            "/joy_set - добавить/убрать черный список и минимальное значение\n" +
            "/joy_url - добавить/убрать URL для отслеживания\n" +
            "/joy_on - включить автоматическую подписку на посты\n" + 
            "/joy_off - отключить автоматическую подписку на посты\n" + 
            "/del_joy - удаляет все ваши данные и сбрасывает настройки.\n" + 
            "Bot created by top fox >:)")

class BotScheduler:
    def __init__(self):
        self.scheduler_thread = None
        self.stop_scheduler_thread = False
        self.sched = BackgroundScheduler()

    def schedule_plan(self, message, selected_chat_id, user_id):
        self.sched.add_job(lambda: send_images_or_gifs(message, selected_chat_id, user_id), 'interval', seconds=TIME_VAR)

    def start_bot(self, message, selected_chat_id, user_id):
        if not self.sched.running:
            self.schedule_plan(message, selected_chat_id, user_id)
            self.sched.start()
            bot.send_message(message.chat.id, "Debug schedule")
    
    def stop_bot(self):
        # Set the flag to stop the scheduler thread
        self.sched.shutdown()
        self.sched = BackgroundScheduler()

class ChatData:
    _id = None
    _title = None
    _content = {} 
    
    
    def __init__(self, id, title):
        self._id = id
        self._title = title
        self._content['blacklist'] = {}
        self._content['id_of_images'] = {}
        self._content['url_list'] = []
        self._content['scheduler'] = BotScheduler()
        self._content['parsing_data'] = {}
        
        
    def __getitem__(self, key):
        return self._content[key]
    
    def __setitem__(self, key, value):
        self._content[key] = value
    
    def __str__(self) -> str:
        return f"Chat {self._id}: {self._content}"
    
    def get_id(self):
        return self._id
        
    def get_title(self):
        return self._title
        

class UserData:
    _chat_ids = {}
    _message = None
    _active = None
    
    def __init__(self, message):
        self._message = message
        self._active = False

    def __str__(self) -> str:
        return f"User: {self._message.from_user.id} - Chat_ids: {[k for k,_ in self._chat_ids.items()]}"
    
    def set_user_to_active(self):
        self._active = True
    
    def is_active(self):
        return self._active
    
    def add_chat(self, message):
        chat_id = message.chat.id
        if chat_id not in self._chat_ids:
            chat_title = message.chat.title if message.chat.title else "Этот чат"
            self._chat_ids[chat_id] = ChatData(chat_id, chat_title)
        else:
            bot.send_message(message.chat.id, "Этот бот уже активирован вами")
            
    def remove_chat(self, message):
        chat_id = message.chat.id
        if chat_id in self._chat_ids:
            del self._chat_ids[chat_id]
    
    def find_chat(self, chat_id):
        return self._chat_ids.get(chat_id)
    
    def get_chat_ids(self):
        return self._chat_ids
        

def get_blacklist(message, chat_id):
    chat_data = user_collection[message.from_user.id].find_chat(chat_id)
    
    if message.text == '.':
        chat_data['blacklist'].clear()
        bot.send_message(message.chat.id, "Настройка завершена. Чтобы заново обновить параметры, напишите команду /joy_set")
    else:
        tags_input = message.text.split(',')
        tags = [tag for tag in tags_input if tag]
        for tag in tags:
            if tag.strip() not in chat_data['blacklist']:
                chat_data['blacklist'][tag.strip()] = 999.0
        bot.send_message(message.chat.id, "0 - Изменить минимальное значение рейтинга для каждого тега \n" + 
                                        "1 - Поставить одно значение для всех тегов \n" +
                                        "2 - Пропустить")
        bot.register_next_step_handler(message, lambda m:blue_and_red(m,chat_id))
        
def blue_and_red(message, chat_id):
    chat_data = user_collection[message.from_user.id].find_chat(chat_id)
    if message.text == '0':
        temp_tags = ""
        for key,value in chat_data['blacklist'].items():
            temp_tags += key + "=" + str(value) + "\n"
        bot.send_message(message.chat.id, temp_tags)
        bot.send_message(message.chat.id, "Введите минимальное значение рейтинга для каждого тега одним сообщением:")
        bot.send_message(message.chat.id, "Примеры: нейронные сети=8.5,волки=5.5,...")
        bot.register_next_step_handler(message, lambda m:set_each_tag_score(m,chat_id))
    elif message.text == '1':
        temp_tags = ""
        for key,value in chat_data['blacklist'].items():
            temp_tags += key + " = " + str(value) + "\n"
        bot.send_message(message.chat.id, temp_tags)
        bot.send_message(message.chat.id, "Введите общее минимальное значение рейтинга для всех постов (пример '5.4'):")
        bot.register_next_step_handler(message, lambda m:set_for_all_tag_score(m,chat_id))
    elif message.text == '2':
        bot.send_message(message.chat.id, "Настройка завершена. Чтобы заново обновить параметры, напишите команду /joy_set")
        return
    else:
        bot.send_message(message.chat.id, "Вы выбрали вариант, которого не было. Вы должны прописать '0','1' или '2'")
        
def set_for_all_tag_score(message,chat_id):
    chat_data = user_collection[message.from_user.id].find_chat(chat_id)
    try:
        test_float = float(message.text)
    except ValueError:
        bot.send_message(message.chat.id, "Введенное сообщение не является значением. Значение для тегов установлено по умолчанию.")
    for key,value in chat_data['blacklist'].items():
        chat_data['blacklist'][key] = float(message.text)
    bot.send_message(message.chat.id, "Настройка завершена. Чтобы заново обновить параметры, напишите команду /joy_set")
    
def set_each_tag_score(message,chat_id):
    chat_data = user_collection[message.from_user.id].find_chat(chat_id)
    input_text = message.text.strip()
    if not all('=' in tag for tag in input_text.split(',')):
        bot.send_message(message.chat.id, "Неправильный формат ввода. Используйте формат: нейронные сети=8.5,волки=5.5,...")
        return

    for i in message.text.split(","):
        key, value = i.split("=")
        chat_data['blacklist'][key] = float(value)
    bot.send_message(message.chat.id, "Настройка завершена. Чтобы заново обновить параметры, напишите команду /joy_set")    

        
def check_user(message):
    if message.from_user.id not in user_collection:
        user_collection[message.from_user.id] = UserData(message)
    
def debug_print(message):
    print(user_collection[message.from_user.id])
    
def post_parcing(post_class : any, user_url):
    tag_lists = post_class.find("h2", class_ = "taglist").find_all("a")
    dict_of_parcing_data = dict()
    dict_of_parcing_data['image_classes']   = post_class.find_all("div", class_ = "image")
    dict_of_parcing_data['username']        = post_class.find("div", class_ = "uhead_nick").find("a").text
    dict_of_parcing_data['username_url']    = user_url + post_class.find("div", class_ = "uhead_nick").find("a").get("href")
    dict_of_parcing_data['rating_value']    = float(post_class.find("span", class_ = "post_rating").find("span").text)
    dict_of_parcing_data['post_url']        = user_url + post_class.find("span", class_ = "link_wr").find("a").get("href")
    dict_of_parcing_data['list_of_titles']  = [tag_list.get("title") for tag_list in tag_lists]
    dict_of_parcing_data['post_id']         = re.search("\d+", post_class.get("id")).group(0)
    dict_of_parcing_data['post_texts']      = post_class.find("div", class_ = "post_content").find_all("p")
    return dict_of_parcing_data
    
def clear_id_of_images(chat_data,post_classes,user_url):
    if user_url not in chat_data['id_of_images']: return
    arr_post_classes = [post_class.get("id") for post_class in post_classes]
    new_id_of_images = [i for i in chat_data['id_of_images'][user_url] if i in arr_post_classes]
    chat_data['id_of_images'][user_url] = new_id_of_images

def send_images_or_gifs(message, selected_chat_id, user_id):
    chat_data = user_collection[int(user_id)].find_chat(int(selected_chat_id))
    for user_url in chat_data['url_list']:
        response = requests.get(user_url)
        if response.status_code == 200:
            print(response.status_code)
            soup = BeautifulSoup(response.text, "html.parser")
            #Searching of all posts on page
            post_classes = soup.find_all("div", class_ = "postContainer")
            clear_id_of_images(chat_data,post_classes,user_url)
            for post_class in post_classes:
                time.sleep(10)
                chat_data['parsing_data'][user_url] = post_parcing(post_class, user_url)
                text_container = ""
                for post_text in chat_data['parsing_data'][user_url]['post_texts']:
                    text = post_text.getText()
                    if not text.isspace():
                        text_container += text + "\n"

                
                post_ready = True

                if user_url in chat_data['id_of_images'] and post_class.get("id") in chat_data['id_of_images'][user_url]:
                    post_ready = False

                for title in chat_data['parsing_data'][user_url]['list_of_titles']:
                    if title in chat_data['blacklist'] and chat_data['blacklist'][title] > chat_data['parsing_data'][user_url]['rating_value']:
                        post_ready = False
                        
                if chat_data['parsing_data'][user_url]['username'] in chat_data['blacklist']:
                    post_ready = False

                if post_ready:
                    text = f"Пост №{chat_data['parsing_data'][user_url]['post_id']}\nCсылка на пост: {chat_data['parsing_data'][user_url]['post_url']}\n\n" + \
                                                      f"Автор поста: {chat_data['parsing_data'][user_url]['username']} ({chat_data['parsing_data'][user_url]['username_url']})" + "\n\n" + \
                                                      f"{text_container}"
                    if len(text) > 4096:
                        bot.send_message(selected_chat_id, text[0:4096],disable_web_page_preview=True)
                        bot.send_message(selected_chat_id, "Пост превышает лимит символов. Читать дальше по ссылке...",disable_web_page_preview=True)
                    else:
                        bot.send_message(selected_chat_id, text, disable_web_page_preview=True)
                    
                    # dict_of_parcing_data['image_classes']   = post_class.find_all("div", class_ = "image")
                    for image_class in chat_data['parsing_data'][user_url]['image_classes']:
                        animations = image_class.find_all("span")
                        imgs = image_class.find_all("img")
                        if animations:
                            for anim in animations:
                                link = anim.find("a")
                                if link:
                                    gif_url = link.get("href")
                                    gif_url = urljoin(user_url, gif_url)
                                    bot.send_animation(selected_chat_id, gif_url)
                        else:
                            for img in imgs:
                                img_url = img.get("src")
                                img_url = urljoin(user_url, img_url)
                                bot.send_photo(selected_chat_id, img_url)
                    # if "гиф анимация" in chat_data['parsing_data'][user_url]['list_of_titles']:
                    #     for image_class in chat_data['parsing_data'][user_url]['image_classes']:
                    #         video = image_class.find_all("span")
                    #         for span in video:
                    #             link = span.find("a")
                    #             if link:
                    #                 gif_url = link.get("href")
                    #                 gif_url = urljoin(user_url, gif_url)
                    #                 bot.send_animation(selected_chat_id, gif_url)
                    # else:
                    #     for image_class in chat_data['parsing_data'][user_url]['image_classes']: 
                    #         imgs = image_class.find_all("img")
                    #         for img in imgs:
                    #             img_url = img.get("src")
                    #             img_url = urljoin(user_url, img_url)
                    #             bot.send_photo(selected_chat_id, img_url)
                    if user_url in chat_data['id_of_images']:
                        chat_data['id_of_images'][user_url].append(post_class.get("id"))
                    else:
                        chat_data['id_of_images'][user_url] = [post_class.get("id")]
                chat_data['parsing_data'][user_url]['list_of_titles'].clear()
        else:
            print(response.status_code)
    original_url_list = chat_data['url_list'][:]
    removed_urls = [url for url in original_url_list if url not in chat_data['url_list']]
    if removed_urls:
        for removed_url in removed_urls:
            if chat_data['parsing_data']:
                del chat_data['parsing_data'][removed_url]
            if chat_data['id_of_images']:
                del chat_data['id_of_images'][removed_url]
            
def user_is_active(message):
    if not user_collection[message.from_user.id].is_active():
        bot.send_message(message.chat.id, "Для начала работы нужно активировать бота с помощью команды /start")
    return user_collection[message.from_user.id].is_active()

@bot.message_handler(commands=['start'])
def start_tbot(message):
    if message.chat.type != "private": return
    check_user(message)
    user_collection[message.from_user.id].set_user_to_active()
    user_collection[message.from_user.id].add_chat(message)
    debug_print(message)
    bot.send_message(message.chat.id, get_start_text())
    
@bot.message_handler(commands=['joy_set'])
def settings(message):
    if message.chat.type != "private": return
    check_user(message)
    if not user_is_active(message): return
    markup = types.InlineKeyboardMarkup()
    if message.from_user.id in user_collection:
        for _,chat_id in user_collection[message.from_user.id].get_chat_ids().items():
            button_text = f"Chat: {chat_id.get_title()}"
            markup.add(types.InlineKeyboardButton(button_text, callback_data=f"^banlist,{chat_id.get_id()},{message.from_user.id}"))
    bot.reply_to(message,"Выберите какой чат настроить:", reply_markup=markup)
    debug_print(message)

@bot.message_handler(commands=['joy_url'])
def set_url(message):
    if message.chat.type != "private": return
    check_user(message)
    if not user_is_active(message): return
    markup = types.InlineKeyboardMarkup()
    if message.from_user.id in user_collection:
        for _,chat_id in user_collection[message.from_user.id].get_chat_ids().items():
            button_text = f"Chat: {chat_id.get_title()}"
            markup.add(types.InlineKeyboardButton(button_text, callback_data=f"^urllist,{chat_id.get_id()},{message.from_user.id}"))
    bot.reply_to(message,"Выберите какой чат настроить:", reply_markup=markup)
    # chat_data = user_collection[message.from_user.id].find_chat(message.chat.id)

@bot.message_handler(commands=['joy_on'])
def start_scheduler(message):
    if message.chat.type != "private": return
    check_user(message)
    if not user_is_active(message): return
    markup = types.InlineKeyboardMarkup()
    if message.from_user.id in user_collection:
        for _,chat_id in user_collection[message.from_user.id].get_chat_ids().items():
            button_text = f"Chat: {chat_id.get_title()}"
            markup.add(types.InlineKeyboardButton(button_text, callback_data=f"^start_scheduler,{chat_id.get_id()},{message.from_user.id}"))
        bot.reply_to(message,"Выберите чат для запуска в нем бота:", reply_markup=markup)

    
@bot.message_handler(commands=['joy_off'])
def start_scheduler(message):
    if message.chat.type != "private": return
    check_user(message)
    if not user_is_active(message): return
    markup = types.InlineKeyboardMarkup()
    if message.from_user.id in user_collection:
        for _,chat_id in user_collection[message.from_user.id].get_chat_ids().items():
            button_text = f"Chat: {chat_id.get_title()}"
            markup.add(types.InlineKeyboardButton(button_text, callback_data=f"^stop_scheduler,{chat_id.get_id()},{message.from_user.id}"))
        bot.reply_to(message,"Выберите чат для остановки в нем бота:", reply_markup=markup)

    
@bot.message_handler(commands=['del_joy'])
def delete_user(message):
    if message.chat.type != "private": return
    check_user(message)
    if not user_is_active(message): return
    bot.send_message(message.chat.id, "Вы уверенны? Ответ:Да/Нет")
    bot.register_next_step_handler(message, lambda m: confirm_delete_user(m))
    
    
def confirm_delete_user(message):
    if message.text == "Да":
        del user_collection[message.from_user.id]
        bot.send_message(message.chat.id, "Все данные были обнуленны")
    else:
        return
    
    
@bot.message_handler(content_types=["new_chat_members"])
def chat_new_member(message):
    check_user(message)
    bot_id = bot.get_me().id
    if any(member.id == bot_id for member in message.new_chat_members):
        user_collection[message.from_user.id].add_chat(message)
        bot.send_message(message.chat.id, "Хвостикую что добавили меня чат! Я готов к работе.")
    debug_print(message)
    
@bot.message_handler(content_types=["left_chat_member"])
def chat_left(message):
    bot_id = bot.get_me().id
    if bot_id == message.left_chat_member.id:
        for _,user_var in user_collection.items():
            user_var.remove_chat(message)

        
@bot.callback_query_handler(func=lambda callback: callback.data.startswith("^start_scheduler"))
def handler_banlist(callback):
    chat_id = callback.message.chat.id
    _, selected_chat_id, user_id = callback.data.split(',')
    chat_data = user_collection[int(user_id)].find_chat(int(chat_id))
    chat_data['scheduler'].start_bot(callback.message, selected_chat_id, user_id)
    bot.send_message(chat_id, "Бот был запущен в выбранном чате:")
    
@bot.callback_query_handler(func=lambda callback: callback.data.startswith("^stop_scheduler"))
def handler_banlist(callback):
    chat_id = callback.message.chat.id
    _, selected_chat_id, user_id = callback.data.split(',')
    chat_data = user_collection[int(user_id)].find_chat(int(chat_id))
    chat_data['scheduler'].stop_bot()
    bot.send_message(chat_id, "Бот был остановлен в выбранном чате:")

#Handler for add/removing tag from blacklist
@bot.callback_query_handler(func=lambda callback: callback.data.startswith("^banlist"))
def handler_banlist(callback):
    chat_id = callback.message.chat.id
    _, selected_chat_id, user_id = callback.data.split(',')
    bot.send_message(chat_id, "Введите теги, которые хотите видеть в черном списке (пример: нейронные сети,волки,...):")
    bot.send_message(chat_id, "По умолчанию новые теги будут иметь порог 999. Если хотите обнулить черный лист, напишите '.'")
    bot.register_next_step_handler(callback.message, lambda m: get_blacklist(m, int(selected_chat_id)))

#Handler for add/removing url from url list
@bot.callback_query_handler(func=lambda callback: callback.data.startswith("^urllist"))
def handler_urllist(callback):
    _, selected_chat_id, user_id = callback.data.split(',')
    handler_logic_url(callback.message, selected_chat_id, user_id)
    
@bot.callback_query_handler(func=lambda callback: callback.data.startswith("^showurl"))
def add_url(callback):
    chat_id = callback.message.chat.id
    _, selected_chat_id, user_id = callback.data.split(',')
    url_strlist = ""
    chat_data = user_collection[int(user_id)].find_chat(int(chat_id))
    if chat_data['url_list']:
        for i, url in enumerate(chat_data['url_list']):
            url_strlist += f"{i}: {url}\n"
        bot.send_message(chat_id, url_strlist, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda callback: callback.data.startswith("^addurl"))
def add_url(callback):
    chat_id = callback.message.chat.id
    _, selected_chat_id, user_id = callback.data.split(',')
    bot.send_message(chat_id, "Пожалуйста, введите URL который хотите добавить. Пример(https://fox.reactor.cc)", disable_web_page_preview=True)
    bot.register_next_step_handler(callback.message, lambda m: confirm_add_url(m, selected_chat_id, user_id))

@bot.callback_query_handler(func=lambda callback: callback.data.startswith("^deleteurl"))
def delete_url(callback):
    chat_id = callback.message.chat.id
    _, selected_chat_id, user_id = callback.data.split(',')
    url_strlist = ""
    chat_data = user_collection[int(user_id)].find_chat(int(chat_id))
    if chat_data['url_list']:
        for i, url in enumerate(chat_data['url_list']):
            url_strlist += f"{i}: {url}\n"
        bot.send_message(chat_id, url_strlist, disable_web_page_preview=True)
        bot.send_message(chat_id, "Выберите значение (то есть напишите в ответ) для удаления URL")
        bot.register_next_step_handler(callback.message, lambda m: remove_url(m, selected_chat_id, user_id))
    else:
        bot.send_message(chat_id, "Список пустой")
    
def remove_url(message, chat_id, user_id):
    chat_data = user_collection[int(user_id)].find_chat(int(chat_id))
    try:
        index = int(message.text)
        if 0 <= index < len(chat_data['url_list']):
            deleted_url = chat_data['url_list'].pop(index)
            bot.send_message(message.chat.id, f"Удаленный URL: {deleted_url}", disable_web_page_preview=True)
        else:
            bot.send_message(message.chat.id, "Неправильное значение. Пожалуйста, введите правильное значение для удаления URL.")
    except ValueError:
        bot.send_message(message.chat.id, "Неправильный тип данных. Пожалуйста, введите правильный тип данных.")

def confirm_add_url(message, chat_id, user_id):
    url = message.text.strip()
    chat_data = user_collection[int(user_id)].find_chat(int(chat_id))

    # Check if the URL is valid
    if is_valid_url(url):
        chat_data['url_list'].append(url)
        bot.send_message(message.chat.id, f"URL '{url}' был добавлен в список", disable_web_page_preview=True)
    else:
        bot.send_message(message.chat.id, "Неправильный URL. Заново введите команду /joy_url.")
    debug_print(message)

def is_valid_url(url):
    try:
        parsed_url = urlparse(url)
        return all([parsed_url.scheme, parsed_url.netloc])
    except Exception as e:
        return False

def handler_logic_url(message, chat_id, user_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Посмотреть список", callback_data=f"^showurl,{chat_id},{user_id}"))
    markup.add(types.InlineKeyboardButton("Добавить", callback_data=f"^addurl,{chat_id},{user_id}"))
    markup.add(types.InlineKeyboardButton("Удалить", callback_data=f"^deleteurl,{chat_id},{user_id}"))
    bot.reply_to(message,"Меню управления URL:", reply_markup=markup)


bot.infinity_polling()