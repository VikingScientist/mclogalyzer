#!/usr/bin/env python2

# Copyright 2013-2015 Moritz Hilscher
#
#  This file is part of mclogalyzer.
#
#  mclogalyzer is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  mclogalyzer is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with mclogalyzer.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import datetime
import gzip
import json
import os
import re
import sys
import time
import numpy
import matplotlib
matplotlib.use('Agg') # force plotter to not use an x-backend
import pylab

import jinja2



REGEX_IP = "(\d+)\.(\d+)\.(\d+)\.(\d+)"

REGEX_LOGIN_USERNAME = re.compile("\[Server thread\/INFO\]: ([^]]+)\[")
REGEX_LOGOUT_USERNAME = re.compile("\[Server thread\/INFO\]: ([^ ]+) lost connection")
REGEX_LOGOUT_USERNAME2 = re.compile(
    "\[Server thread\/INFO\]:.*GameProfile.*name='?([^ ,']+)'?.* lost connection")
REGEX_KICK_USERNAME = re.compile("\[INFO\] CONSOLE: Kicked player ([^ ]*)")
REGEX_ACHIEVEMENT = re.compile("\[Server thread\/INFO\]: ([^ ]+) has just earned the achievement \[(.*)\]")

# regular expression to get the username of a chat message
# you need to change this if you have special chat prefixes or stuff like that
# this regex works with chat messages of the format: <prefix username> chat message
REGEX_CHAT_USERNAME = re.compile("\[Server thread\/INFO\]: <([^>]* )?([^ ]*)> (\w+)")

DEATH_MESSAGES = (
    "was squashed by.*",
    "was pricked to death",
    "walked into a cactus whilst trying to escape.*",
    "drowned.*",
    "blew up",
    "was blown up by.*",
    "fell from a high place.*",
    "hit the ground too hard",
    "fell off a ladder",
    "fell off some vines",
    "fell out of the water",
    "fell into a patch of.*",
    "was doomed to fall.*",
    "was shot off.*",
    "was blown from a high place.*",
    "went up in flames",
    "burned to death",
    "was burnt to a crisp whilst fighting.*",
    "walked into a fire whilst fighting.*",
    "was slain by.*",
    "was shot by.*",
    "was fireballed by.*",
    "was killed.*",
    "got finished off by.*",
    "tried to swim in lava.*",
    "died",
    "was struck by lighting",
    "starved to death",
    "suffocated in a wall",
    "was pummeled by.*",
    "fell out of the world",
    "was knocked into the void.*",
    "withered away",
)

REGEX_DEATH_MESSAGES = set()
for message in DEATH_MESSAGES:
    REGEX_DEATH_MESSAGES.add(re.compile("\Server thread\/INFO\]: ([^ ]+) (" + message + ")"))

# Will have to update this when number of achievements change.
# Got this value from http://minecraft.gamepedia.com/Achievements
ACHIEVEMENTS_AVAILABLE = 34

# Maximum duration, in seconds, that a logout can be considered a ragequit
RAGEQUIT_MAX_ELAPSED_TIME = 45

def capitalize_first(str):
    if not len(str):
        return ""
    return str[:1].upper() + str[1:]


class UserStats:
    def __init__(self, username=""):
        self._username = username
        self._logins = 0

        self._day_activity  = {}
        self._hour_activity = [0]*24
        self._prev_login   = None
        self._first_login  = None
        self._last_login   = None
        self._time = datetime.timedelta()
        self._longest_session = datetime.timedelta()

        self._death_count = 0
        self._death_types = {}

        # Rage quit tracking
        self._last_death_time = None
        self._ragequits = 0

        self._messages = 0

        self._achievement_count = 0
        self._achievements = []

    def handle_logout(self, date):
        if self._prev_login is None:
            return
        days, hours = get_time_distribution(self._prev_login, date)
        # days is a dictionary of all dates (keys) and play minutes
        for d in days:
            if d in self._day_activity:
                self._day_activity[d] += days[d] 
            else:
                self._day_activity[d]  = days[d]
        # hours is an array of 24 elements
        for i in range(len(hours)):
            self._hour_activity[i] += hours[i]
        session = date - self._prev_login
        self._time += session
        self._longest_session = max(self._longest_session, session)
        self._prev_login = None
        self.track_ragequits(date)


    def track_ragequits(self, date):
        if self._last_death_time:
            elapsed_death_to_logout = (date - self._last_death_time).total_seconds()
            if elapsed_death_to_logout <= RAGEQUIT_MAX_ELAPSED_TIME:
                self._ragequits += 1

        self._last_death_time = None

    def make_plots(self, width, height):
        print 'Creating figures for user ', self._username
        # make daytime distribution plot
        pylab.figure(1, figsize=(width/100.0, height/100.0))
        x = []
        for i in range(24):
            x.append(datetime.datetime(2001, 1,1, hour=i))
        justHours = matplotlib.dates.DateFormatter('%H:%M')
        pylab.plot(x, self._hour_activity, 'o-')
        pylab.gca().xaxis.set_major_formatter(justHours)
        pylab.xlabel('Clock');
        pylab.ylabel('Minutes played');
        pylab.title('Daytime play distribution')
        pylab.savefig('img/'+self._username+'_daytime_dist.png')
        pylab.clf()

        # playtime by day
        today = datetime.date.today().toordinal()
        start_date = today
        for date in self._day_activity:
            start_date = min(start_date, date)
        n_days   = today - start_date + 1
        n_month  = datetime.date.today().day
        n_week   = datetime.date.today().weekday() + 1
        playtime = [0]*n_days
        weektime = [0]*7
        date_tag = []
        for i in range(n_days):
            date_tag.append(datetime.datetime.fromordinal(start_date + i))
        for date in self._day_activity:
            playtime[date-start_date]                            = self._day_activity[date]
            weektime[datetime.date.fromordinal(date).weekday()] += self._day_activity[date]

        # playtime by day (all history)
        pylab.plot(date_tag, playtime, '.-')
        pylab.setp(pylab.xticks()[1], rotation=20)
        pylab.gca().xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%b %d %Y'))
        pylab.xlabel('Date');
        pylab.ylabel('Minutes played');
        pylab.title('Play minutes per day')
        pylab.savefig('img/'+self._username+'_day_history.png')
        pylab.clf()

        # playtime by day (current month)
        pylab.plot(date_tag[-n_month:], playtime[-n_month:], '.-')
        pylab.setp(pylab.xticks()[1], rotation=20)
        pylab.gca().xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%b %d %Y'))
        pylab.xlabel('Date');
        pylab.ylabel('Minutes played');
        pylab.title('Play minutes per day this month')
        pylab.savefig('img/'+self._username+'_day_month.png')
        pylab.clf()

        # playtime by day (current week)
        pylab.plot(date_tag[-n_week:], playtime[-n_week:], '.-')
        pylab.setp(pylab.xticks()[1], rotation=20)
        pylab.gca().xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%b %d %Y'))
        pylab.gca().xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(n_week+1))
        pylab.gca().xaxis.set_minor_locator(matplotlib.ticker.MaxNLocator(1))
        matplotlib.ticker.MaxNLocator
        pylab.xlabel('Date');
        pylab.ylabel('Minutes played');
        pylab.title('Play minutes per day this week')
        pylab.savefig('img/'+self._username+'_day_week.png')
        pylab.clf()

        # plot weekday pie chart
        labels = 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'
        explode= [.03]*7
        pylab.pie(weektime[::-1], explode=explode, labels=labels[::-1], autopct='%1.1f%%', shadow=True)
        pylab.title('Playtime per weekday') #, bbox={'facecolor':'0.8', 'pad':5})
        pylab.savefig('img/'+self._username+'_weekday_pie.png')
        pylab.clf()

    @property
    def username(self):
        return self._username

    @property
    def logins(self):
        return self._logins

    @property
    def time(self):
        return format_delta(self._time)

    @property
    def time_per_login(self):
        return format_delta(
            self._time / self._logins if self._logins != 0 else datetime.timedelta(), False)

    @property
    def active_days(self):
        return len(self._day_activity)

    @property
    def time_per_active_day(self):
        return format_delta(
            self._time / self.active_days if self.active_days != 0 else datetime.timedelta(), False)

    @property
    def first_login(self):
        return str(self._first_login)

    @property
    def last_login(self):
        return str(self._last_login)

    @property
    def longest_session(self):
        return format_delta(self._longest_session, False)

    @property
    def messages(self):
        return self._messages

    @property
    def time_per_message(self):
        if self._messages == 0:
            return "<div class='text-center'>-</div>"
        return format_delta(
            self._time / self._messages if self._messages != 0 else datetime.timedelta())

    @property
    def death_count(self):
        return self._death_count

    @property
    def death_types(self):
        return sorted(self._death_types.items(), key=lambda k: k[1])

    @property
    def achievement_count(self):
        return self._achievement_count

    @property
    def achievements(self):
        return sorted(self._achievements)

    @property
    def ragequit_count(self):
        return self._ragequits

class ChatLog:
    def __init__(self, timestamp, user, msg):
        self._time    = str("%02d:%02d:%02d"%(timestamp.hour,timestamp.minute,timestamp.second))
        self._user    = user
        self._message = msg

    @property
    def time(self):
        return self._time
    @property
    def user(self):
        return self._user
    @property
    def message(self):
        return self._message

class ChatDay:
    def __init__(self, timestamp):
        self._date     = str("%d-%02d-%02d"%(timestamp.year,timestamp.month,timestamp.day))
        self._chat     = []
        self._even_day = False;

    # list of all chat messages on this day
    @property
    def chat(self):
        return self._chat

    # date of chat history
    @property
    def date(self):
        return self._date

    # tag every other day to increase readability on final document
    @property
    def even_day(self):
        return self._even_day
    

class ServerStats:
    def __init__(self):
        self._statistics_since = None
        self._time_played = datetime.timedelta()
        self._max_players = 0
        self._max_players_date = None
        self._include_figures = False

    @property
    def statistics_since(self):
        return self._statistics_since

    @property
    def time_played(self):
        return format_delta(self._time_played, True, True)

    @property
    def max_players(self):
        return self._max_players

    @property
    def max_players_date(self):
        return self._max_players_date

    @property
    def include_figures(self):
        return self._include_figures


def grep_logname_date(line):
    try:
        d = time.strptime("-".join(line.split("-")[:3]), "%Y-%m-%d")
    except ValueError:
        return None
    return datetime.date(*(d[0:3]))


def grep_log_datetime(date, line):
    try:
        d = time.strptime(line.split(" ")[0], "[%H:%M:%S]")
    except ValueError:
        return None
    return datetime.datetime(
        year=date.year, month=date.month, day=date.day,
        hour=d.tm_hour, minute=d.tm_min, second=d.tm_sec
    )


def grep_login_username(line):
    search = REGEX_LOGIN_USERNAME.search(line)
    if not search:
        print "### Warning: Unable to find login username:", line
        return ""
    username = search.group(1).lstrip().rstrip()
    return username.decode("ascii", "ignore").encode("ascii", "ignore")


def grep_logout_username(line):
    search = REGEX_LOGOUT_USERNAME.search(line)
    if not search:
        search = REGEX_LOGOUT_USERNAME2.search(line)
        if not search:
            print "### Warning: Unable to find username:", line
            return ""
    username = search.group(1).lstrip().rstrip()
    return username.decode("ascii", "ignore").encode("ascii", "ignore")


def grep_kick_username(line):
    search = REGEX_KICK_USERNAME.search(line)
    if not search:
        print "### Warning: Unable to find kick logout username:", line
        return ""
    return search.group(1)[:-1].decode("ascii", "ignore").encode("ascii", "ignore")


def grep_death(line):
    for regex in REGEX_DEATH_MESSAGES:
        search = regex.search(line)
        if search:
            return search.group(1), capitalize_first(search.group(2))
    return None, None

def grep_chatlog(line):
    search

def grep_achievement(line):
    search = REGEX_ACHIEVEMENT.search(line)
    if not search:
        print "### Warning: Unable to find achievement username or achievement:", line
        return None, None
    username = search.group(1)
    return username.decode("ascii", "ignore").encode("ascii", "ignore"), search.group(2)

# returns number of minutes played during each clock hour and date between start and end
def get_time_distribution(start, end):
    hours = [0]*24
    days  = {}
    timeleft = end-start
    time_iterate = start
    while time_iterate < end:
        next_hour = (time_iterate + datetime.timedelta(hours=1)).replace(minute=0, second=0)
        dt = min(next_hour-time_iterate, end-time_iterate)
        hours[time_iterate.hour]  += dt.seconds/60.0
        dateKey = time_iterate.date().toordinal()
        if dateKey in days:
            days[dateKey] += dt.seconds/60.0
        else:
            days[dateKey]  = dt.seconds/60.0
        time_iterate += dt
    return days, hours

def format_delta(timedelta, days=True, maybe_years=False):
    seconds = timedelta.seconds
    hours = seconds // 3600
    seconds = seconds - (hours * 3600)
    minutes = seconds // 60
    seconds = seconds - (minutes * 60)
    fmt = "%02dh %02dm %02ds" % (hours, minutes, seconds)
    if days:
        if maybe_years:
            days = timedelta.days
            years = days // 365
            days = days - (years * 365)
            if years > 0:
                return ("%d years, %02d days" % (years, days)) + fmt
        return ("%02d days, " % (timedelta.days)) + fmt
    return fmt


def parse_whitelist(whitelist_path):
    json_data = json.load(open(whitelist_path))
    return map(lambda x: x["name"], json_data)


def parse_logs(logdir, since=None, whitelist_users=None):
    users = {}
    chat = []
    server = ServerStats()
    online_players = set()

    first_date = None
    for logname in sorted(os.listdir(logdir)):
        if not re.match("\d{4}-\d{2}-\d{2}-\d+\.log\.gz", logname):
            continue

        today = grep_logname_date(logname)
        thisChatDay = ChatDay(today)
        if first_date is None:
            first_date = today
        print "Parsing log %s (%s) ..." % (logname, today)

        logfile = gzip.open(os.path.join(logdir, logname))

        for line in logfile:
            line = line.rstrip()

            if "logged in with entity id" in line:
                date = grep_log_datetime(today, line)
                if date is None or (since is not None and date < since):
                    continue

                username = grep_login_username(line)
                if not username:
                    continue

                if whitelist_users is None or username in whitelist_users:
                    if username not in users:
                        users[username] = UserStats(username)
                    user = users[username]
                    user._logins += 1
                    user._last_login = user._prev_login = date
                    if user._first_login is None:
                        user._first_login = date

                    online_players.add(username)
                    if len(online_players) > server._max_players:
                        server._max_players = len(online_players)
                        server._max_players_date = date

            elif "lost connection" in line or "[INFO] CONSOLE: Kicked player" in line:
                date = grep_log_datetime(today, line)
                if date is None or (since is not None and date < since):
                    continue

                username = ""
                if "lost connection" in line:
                    username = grep_logout_username(line)
                else:
                    username = grep_kick_username(line)

                if not username or username.startswith("/"):
                    continue
                if username not in users:
                    continue

                user = users[username]
                user.handle_logout(date)
                if username in online_players:
                    online_players.remove(username)

            elif "Stopping server" in line or "forcibly shutdown" in line or "Starting minecraft server" in line:
                date = grep_log_datetime(today, line)
                if date is None or (since is not None and date < since):
                    continue

                for user in users.values():
                    user.handle_logout(date)
                online_players = set()

            elif "earned the achievement" in line:
                achievement_username, achievement = grep_achievement(line)
                if achievement_username is not None:
                    if achievement_username in users:
                        achievement_user = users[achievement_username]
                        achievement_user._achievement_count += 1
                        achievement_user._achievements.append(achievement)
            else:
                death_username, death_type = grep_death(line)
                death_time = grep_log_datetime(today, line)
                if death_username is not None:
                    if death_username in users:
                        death_user = users[death_username]
                        death_user._last_death_time = death_time
                        death_user._death_count += 1
                        if death_type not in death_user._death_types:
                            death_user._death_types[death_type] = 0
                        death_user._death_types[death_type] += 1
                else:
                    date = grep_log_datetime(today, line)
                    search = REGEX_CHAT_USERNAME.search(line)
                    if not search:
                        continue
                    username = search.group(2)
                    chat_message = search.group(3)
                    if username in users:
                        users[username]._messages += 1
                    thisChatDay._chat.append(ChatLog(date, username, chat_message))

        if len(thisChatDay._chat) > 0:
            chat.append(thisChatDay)
            thisChatDay._chat = thisChatDay._chat[::-1] # reverse chat list so newest on top
    chat = chat[::-1]

    if whitelist_users is not None:
        for username in whitelist_users:
            if username not in users:
                users[username] = UserStats(username)

    users = users.values()
    users.sort(key=lambda user: user.time, reverse=True)

    server._statistics_since = since if since is not None else first_date
    for user in users:
        server._time_played += user._time

    for i in range(len(chat)):
        if i%2:
            chat[i]._even_day = True
        else:
            chat[i]._even_day = False

    return users, server, chat


def main():
    parser = argparse.ArgumentParser(
        description="Analyzes the Minecraft Server Log files and generates some statistics.")
    parser.add_argument("-t", "--template",
                        help="the template to generate the output file",
                        metavar="template")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--since",
                       help="ignores the log before this date, must be in format year-month-day hour:minute:second",
                       metavar="<datetime>")
    group.add_argument("--month",
                       action='store_true', help="create report of last month")
    group.add_argument("--week",
                       action='store_true', help="create report of last week")
    parser.add_argument("-w", "--whitelist",
                        help="the whitelist of the server (only use included usernames)",
                        metavar="<whitelist>")
    parser.add_argument("--chat",
                        action='store_true',
                        help="display the general chat log")
    parser.add_argument("--figures",
                        action='store_true',
                        help="generate statistic figures (stored in \"img\" folder)")
    parser.add_argument("--figuresize",
                        nargs=2,
                        default=(800,600),
                        type=int,
                        help="figure size (in pixels) for all generated plots",
                        metavar="<width height>")
    parser.add_argument("logdir",
                        help="the server log directory",
                        metavar="<logdir>")
    parser.add_argument("output",
                        help="the output html file",
                        metavar="<outputfile>")
    args = vars(parser.parse_args())

    since = None
    if args['month']:
        since = datetime.datetime.now() - datetime.timedelta(days=30)
    elif args['week']:
        since = datetime.datetime.now() - datetime.timedelta(days=7)
    elif args['since'] is not None:
        try:
            d = time.strptime(args["since"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            print "Invalid datetime format! The format must be year-month-day hour:minute:second ."
            sys.exit(1)
        since = datetime.datetime(*(d[0:6]))

    whitelist_users = parse_whitelist(args["whitelist"]) if args["whitelist"] else None
    users, server, chats = parse_logs(args["logdir"], since, whitelist_users)

    if not args['chat']:
        chats = [] # ignore chat messages
    if args['figures']:
        if not os.path.isdir('img'):
            os.makedirs('img') # should include error testing if process does not have the right write permissions
        server._include_figures = True
        figure_width  = args['figuresize'][0]
        figure_height = args['figuresize'][1]

    template_path = os.path.join(os.path.dirname(__file__), "template.html")
    if args["template"] is not None:
        template_path = args["template"]
    template_dir = os.path.dirname(template_path)
    template_name = os.path.basename(template_path)
    if not os.path.exists(template_path):
        print "Unable to find template file %s!" % template_path
        sys.exit(1)

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir))
    template = env.get_template(template_name)
    
    if server._include_figures:
        for u in users:
            u.make_plots(figure_width, figure_height)

    f = open(args["output"], "w")
    f.write(template.render(users=users,
                            server=server,
                            chats=chats,
                            achievements_available=ACHIEVEMENTS_AVAILABLE,
                            last_update=time.strftime("%Y-%m-%d %H:%M:%S")))
    f.close()


if __name__ == "__main__":
    main()
