import os
import requests
import json
import collections
from bilibili import Uploader
import time
from apscheduler.schedulers.blocking import BlockingScheduler
from cq import bot
from multiprocessing import Process
from videoutils import snapshot

#region 日志通用

import logging
from logging import handlers

class Logger(object):
    level_relations = {
        'debug':logging.DEBUG,
        'info':logging.INFO,
        'warning':logging.WARNING,
        'error':logging.ERROR,
        'crit':logging.CRITICAL
    }#日志级别关系映射

    def __init__(self,filename,level='info',when='D',backCount=3,fmt='%(asctime)s - %(filename)s[line:%(lineno)d] - %(levelname)s: %(message)s'):
        self.logger = logging.getLogger(filename)
        format_str = logging.Formatter(fmt)#设置日志格式
        self.logger.setLevel(self.level_relations.get(level))#设置日志级别
        sh = logging.StreamHandler()#往屏幕上输出
        sh.setFormatter(format_str) #设置屏幕上显示的格式
        th = handlers.TimedRotatingFileHandler(filename=filename,when=when,backupCount=backCount,encoding='utf-8')#往文件里写入#指定间隔时间自动生成文件的处理器
        #实例化TimedRotatingFileHandler
        #interval是时间间隔，backupCount是备份文件的个数，如果超过这个个数，就会自动删除，when是间隔的时间单位，单位有以下几种：
        # S 秒
        # M 分
        # H 小时、
        # D 天、
        # W 每星期（interval==0时代表星期一）
        # midnight 每天凌晨
        th.setFormatter(format_str)#设置文件里写入的格式
        self.logger.addHandler(sh) #把对象加到logger里
        self.logger.addHandler(th)

SPLIT_LINE1 = "\n------------------------------------------------------------------------------------------------------------------"
SPLIT_LINE2 = "\n=================================================================================================================="

#endregion

CID = "写上T站的clientID"

log = Logger('twitch-bilibili' + time.strftime('%Y-%m-%d',time.localtime(time.time())) + '.log',level='debug')

TWITCH_GROUP=385181382

class TwitchUser(object):
    def __init__(self, id, is_live):
        self.__id = id
        self.is_live = is_live

    def get_id(self):
        return self.__id

    def __str__(self):
        return "用户：{}，直播情况：{}".format(self.__id, self.is_live)

class TwitchVideo(object):
    def __init__(self, id, name, url, create_time, publish_time, duration, title):
        self.__id = id
        self.name = name
        self.url = url
        self.create_time = create_time
        self.publish_time = publish_time
        self.duration = duration
        self.title = title

    def get_id(self):
        return self.__id

    def __str__(self):
        return "录像链接：{}\n" \
               "标题：{}\n" \
               "发布时间：{}\n" \
               "时长：{}\n".format(self.url, self.title, self.publish_time, self.duration)

class Twitch(object):
    def __init__(self, client_id):
        self.__client_id = client_id
        self.__proxies = {
             'http':'127.0.0.1:1080',
             'https':'127.0.0.1:1080'
        }
        self.__live_status = collections.OrderedDict()

    def add_name(self, name):
        if name not in self.__live_status:
            id = self._get_user_id(name)
            user = TwitchUser(id=id, is_live=self._check_stream(id))
            self.__live_status[name] = user
            log.logger.info("{} 加入监控".format(name))
            print(user)
        else:
            log.logger.info("{} 已加入，忽略此操作".format(name))

    def delete_name(self, name):
        if name in self.__live_status:
            self.__live_status.pop(name)
            log.logger.info("{} 移除监控".format(name))
        else:
            log.logger.info("{} 未加入，忽略此操作".format(name))

    def _get_user_id(self, name):
        response = requests.get(url="https://api.twitch.tv/helix/users?login={}".format(name),
                                headers={"Client-ID": self.__client_id},
                                proxies=self.__proxies,
                                timeout = 10)
        return json.loads(response.text)["data"][0]["id"]

    def _get_last_video(self, id):
        response = requests.get(url="https://api.twitch.tv/helix/videos?user_id={}&first=1".format(id),
                                headers={"Client-ID": self.__client_id},
                                proxies=self.__proxies,
                                timeout = 10)
        data = json.loads(response.text)["data"][0]
        return data["id"]

    def _get_video(self, vid):
        response = requests.get(url="https://api.twitch.tv/helix/videos?id={}".format(vid),
                                headers={"Client-ID": self.__client_id},
                                proxies=self.__proxies,
                                timeout = 10)
        data = json.loads(response.text)["data"][0]
        video = TwitchVideo(data["id"], data["user_name"], data["url"], self.__utc2local(data["created_at"]), self.__utc2local_sec(data["published_at"]), data["duration"], data["title"])
        log.logger.info("已获取视频信息:\n{}".format(str(video)))
        bot.send_group_msg(group_id=TWITCH_GROUP, message="已获取视频信息:\n{}".format(str(video)))
        return video

    def _check_stream(self, id):
        response = requests.get(url="https://api.twitch.tv/helix/streams?user_id={}".format(id),
                                headers={"Client-ID": self.__client_id},
                                proxies=self.__proxies,
                                timeout = 10)
        return len(json.loads(response.text)["data"]) != 0

    # 检查所有stream
    def check_streams(self):
        log.logger.info("开始检查直播情况")
        bot.send_group_msg(group_id=TWITCH_GROUP, message="开始检查直播情况")
        for name in self.__live_status:
            user = self.__live_status[name]
            is_live = self._check_stream(user.get_id())
            was_live = user.is_live
            user.is_live = is_live
            if bool(was_live) != bool(is_live):
                log.logger.info("{} 直播{}".format(name, "开始" if is_live else "结束"))
                bot.send_group_msg(group_id=TWITCH_GROUP, message="{} 直播{}".format(name, "开始" if is_live else "结束"))
                if not is_live:
                    p = Process(target=self._reprint, args=(user, ))
                    p.start()
            log.logger.info("{} 直播状态：{}".format(name, "直播中" if is_live else "未直播"))
            bot.send_group_msg(group_id=TWITCH_GROUP, message="{} 直播状态：{}".format(name, "直播中" if is_live else "未直播"))

    def _reprint(self, user):
        vid = self._get_last_video(user.get_id())
        video = self._get_video(vid)
        log.logger.info("开始下载视频，地址：{}".format(video.url))
        bot.send_group_msg(group_id=TWITCH_GROUP, message="开始下载视频，地址：{}".format(video.url))
        self._download_video(video)
        log.logger.info("开始上传视频")
        bot.send_group_msg(group_id=TWITCH_GROUP, message="开始上传视频")
        self._upload_video(video)

    def reprint_force(self, vid, skip_download = False):
        video = self._get_video(vid)
        if not skip_download:
            log.logger.info("开始下载视频，地址：{}".format(video.url))
            bot.send_group_msg(group_id=TWITCH_GROUP, message="开始下载视频，地址：{}".format(video.url))
            self._download_video(video)
        log.logger.info("开始上传视频")
        bot.send_group_msg(group_id=TWITCH_GROUP, message="开始上传视频")
        self._upload_video(video)

    def _download_video(self, video):
        os.system('streamlink -o {}.flv --http-proxy 127.0.0.1:1080 --https-proxy 127.0.0.1:1080 --force {} best'.format(video.get_id(), video.url))
        log.logger.info("下载完成")
        bot.send_group_msg(group_id=TWITCH_GROUP, message="下载完成")

    def _upload_video(self, video):
        snapshot(video.get_id(), "00:00:15")
        log.logger.info("生成缩略图完成")
        bot.send_group_msg(group_id=TWITCH_GROUP, message="生成缩略图完成")
        uper = Uploader()
        uper.upload('{}.flv'.format(video.get_id()), '{} {} 直播录像'.format(video.name, video.create_time), 136, "idke,OSU,直播,录像", "{}直播间：https://www.twitch.tv/{}\n{}".format(video.name, video.name, str(video)), "", '{}.jpg'.format(video.get_id()))
        log.logger.info("上传完成")
        bot.send_group_msg(group_id=TWITCH_GROUP, message="上传完成")
        os.remove('{}.flv'.format(video.get_id()))
        log.logger.info("删除文件完成")
        bot.send_group_msg(group_id=TWITCH_GROUP, message="删除文件完成")

    def __utc2local(self, utc_time_str):
        import dateutil.parser
        import pytz
        from datetime import datetime
        return datetime.strftime(dateutil.parser.parse(utc_time_str).astimezone(pytz.timezone('Asia/Shanghai')),
                                 "%Y-%m-%d")

    def __utc2local_sec(self, utc_time_str):
        import dateutil.parser
        import pytz
        from datetime import datetime
        return datetime.strftime(dateutil.parser.parse(utc_time_str).astimezone(pytz.timezone('Asia/Shanghai')),
                                 "%Y-%m-%d %H:%M:%S")

if __name__=="__main__":
    t = Twitch(CID)
    t.add_name("idke")
    sched = BlockingScheduler()
    sched.add_job(t.check_streams, 'cron', minute='*/20')
    print("开始计划")
    sched.start()