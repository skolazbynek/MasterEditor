import datetime
import time
import re
import sys, argparse

import praw
import googleapiclient.discovery
import keyring

# GLOBAL VARIABLES
#   PASSWORDS
CLIENT_ID = keyring.get_password('MasterEditor', 'client-id')
CLIENT_SECRET = keyring.get_password('MasterEditor', 'client-secret')
REDDIT_PASSWORD = keyring.get_password('MasterEditor', 'reddit-password')
REDDIT_USERNAME = keyring.get_password('MasterEditor', 'reddit-username')
YOUTUBE_KEY = keyring.get_password('MasterEditor', 'youtube-key')

#   UTILITIES
ACTIVITY_CHECK = False
FORCE_DAILY_CHECK = False


# FUNCTIONS
def initialize_reddit():
    try:
        reddit = praw.Reddit(client_id=CLIENT_ID,
                             client_secret=CLIENT_SECRET,
                             user_agent='AMVBot:v0.0.0 (by u/Zbynasuper)',
                             username=REDDIT_USERNAME,
                             password=REDDIT_PASSWORD)
    except praw.exceptions.RedditAPIException as exception:
        for subexception in exception.items:
            log(f'Reddit initialization failed. Error: {subexception.error_type}')
    return reddit


def post_feedback_megathread(subreddit_name='amv'):
    reddit = initialize_reddit()
    sub = reddit.subreddit(subreddit_name)

    with open('megathread_template.txt', mode='r', encoding='utf-8') as megathread_template:
        selftext = f'# FEEDBACK MEGATHREAD\n\n# {datetime.date.today().strftime("%B %Y")}\n\n{megathread_template.read()} '

        megathread = sub.submit(title=f'Feedback MEGAthread - {datetime.date.today().strftime("%B %Y")}',
                                selftext=selftext)

    widgets = sub.widgets
    for widget in widgets.sidebar:
        if widget.shortName == 'Megathreads':
            for button in widget:
                if 'Feedback' in button.text:
                    old_megathread = reddit.submission(url=button.url)
                    break
            break

    if old_megathread.stickied:
        old_megathread.mod.sticky(state=False)

    megathread.mod.sticky()
    megathread.mod.flair(text='Megathread', flair_template_id='23f368e6-f498-11e7-8211-0e87da16ebac')
    megathread.mod.suggested_sort(sort='new')

    sidebar_before, _, sidebar_after = sub.description.partition(f'{old_megathread.url}')
    new_sidebar = sidebar_before + megathread.url + sidebar_after
    sub.mod.update(description=new_sidebar)

    new_button = button.__dict__
    new_button['url'] = megathread.url
    new_button.pop('_reddit')
    widget.mod.update(buttons=[new_button])
    return megathread


def check_youtube_video_length(videoURL):
    if args.verbosity:
        log(f'Checking length of video at address {videoURL}.')
    if '//youtu.be' in videoURL:
        _, _, videoID = videoURL.rpartition('//youtu.be/')
    elif 'youtube' in videoURL:
        _, _, videoID = videoURL.rpartition('v=')
        videoID, _, _ = videoID.partition('&')
    else:
        raise AttributeError('Link is not a youtube video.')

    youtube = googleapiclient.discovery.build('youtube', 'v3', developerKey=YOUTUBE_KEY)
    request = youtube.videos().list(part='contentDetails', id=videoID)
    response = request.execute()

    duration = response['items'][0]['contentDetails']['duration']
    if args.verbosity:
        log(f'Duration of the video: {duration}')
    return duration


def remove_submission(submission, reason):
    if args.test:
        log(f'The submission {submission.title} ({submission.shortlink}) would be removed because of: {reason}, but test mode is ON.')
        return True
    log(f'The submission {submission.title} ({submission.shortlink}) was removed because: {reason}')
    removal_comment = submission.reply(f'Your submission has been removed because of following reason: {reason}'
                                       f'\n '
                                       f'\n Beep Boop, this action was perfomed by a bot. If you believe this was a mistake, please message the moderators of this subreddit with a link to this submission.')
    removal_comment.mod.distinguish(how='yes', sticky=True)
    submission.mod.remove()
    return True


def author_activity_check(submission):
    if args.verbosity:
        log('Performing check for author\'s past activity on subreddit.')
    author = submission.author
    comment_count = 0
    try:
        for comment in author.comments.new(limit=None):
            if comment.subreddit_id == 't5_2qpg3':
                if args.verbosity:
                    log(f'A comment has been found on submission {comment.submission.title} - ({comment.submission.shortlink}).')
                comment_count += 1
                if comment_count == 6:
                    if args.verbosity:
                        log('Author has passed the activity check.')
                    return True
            elif int(comment.created_utc) + 15778800 <= int(time.time()):  # If the comment is older than 6 months
                return False
    except StopIteration:
        return False


def daily_checks():
    if args.verbosity:
        log('Performing daily checks.')
    # Reset crash counter to avoid unnecessary crashes in a long run
    global times_crashed
    times_crashed = 0
    # Checks for a first day in a month
    today = datetime.date.today()
    if today.day == 1:
        megathread = post_feedback_megathread()
        log(f'Feedback MEGAthread posted: ({megathread.shortlink})')
    return True


def regular_moderation(submission):
    if args.verbosity:
        log(f'Running moderation loop on submission {submission.title} - ({submission.shortlink})\n'
            f'Checking if the submission is from a mod, approved contributor or already approved manually.')
    # If it's approved, from a moderator or approved submitter, then don't moderate it
    author = submission.author
    mod_check = subreddit.moderator(redditor=author)
    contributor_check = subreddit.contributor(redditor=author)
    if submission.approved or (mod_check.children.__len__() > 0):
        if args.verbosity:
            log('Not moderating, moving on...')
        return True

    try:
        next(contributor_check)
        if args.verbosity:
            log('Not moderating, moving on...')
        return True
    except StopIteration:
        if args.verbosity:
            log('Passing submission to moderation.')
        pass

    if not author_activity_check(submission):
        if ACTIVITY_CHECK:
            remove_submission(submission, 'You have less than 6 comments in last 6 months on this subreddit.')
            return True
        elif args.verbosity:
            log('Author\'s activity check has failed, but the feature is not yet active so moving on...')

    if args.verbosity:
        log('Checking account age.')
    if (int(time.time()) - author.created_utc) < 259200:  # Account younger than 3 days
        remove_submission(submission,
                          f'Your account needs to be at least 3 days old to be able to post on the main page. \n'
                          f'If you are not posting an AMV and need an exception (e.g. contest announcement), please message the mods.')
        return True

    # Video length checking
    #   If submission is a link, hopefully to youtube
    if not (submission.is_self or submission.is_video):
        if args.verbosity:
            log('Submission is a link, checking if it\'s a video and it\'s length.')
        try:
            duration = check_youtube_video_length(submission.url)
            if 'M' not in duration:
                remove_submission(submission,'Video is too short. We only allow videos longer than 1 minute on the main page.')
                return True
        except AttributeError:
            submission.report(
                'Check manually, link being shared is NOT youtube.')  # If not link to youtube, report for manual check
            log(f'Submission \"{submission.title}\" ({submission.shortlink}) has been reported as the link is not Youtube.')
        except IndexError:
            remove_submission(submission, 'Youtube video is being blocked or unaccessible.')
            return True

    #   If submission is a reddit video
    elif submission.is_video:
        if args.verbosity:
            log('Submission is a reddit video, checking length.')
        if submission.media['reddit_video']['duration'] <= 60:
            remove_submission(submission,
                              'Video is too short. We only allow videos longer than 1 minute on the main page.')
            return True

    # Title check
    if args.verbosity:
        log('Checking title of the submission.')
    if re.findall(r'[A-Z]{5}', submission.title):
        remove_submission(submission, 'Title contains excessive Caps Lock.')
        return True
    elif re.findall(r'[^\sa-zA-Z0-9,.“”:;\-\'!?|\"&*+/=^_\[\]()]', submission.title):
        remove_submission(submission, 'Non-standard and\or non-english characters used in the title.')
        return True

    if args.verbosity:
        log('Passed all moderation checks, checking if 24 hours passed from last daily check')

    # Run daily checks if at least 24 hours since last check
    global timer
    if time.time() - 86400 > timer or FORCE_DAILY_CHECK:
        timer = time.time()
        daily_checks()

        # TODO copy other stuff from Automod
        # TODO account karma/age gate - number of comments in subreddit in last 6 months


def log(log_message):
    print(log_message)
    if args.logging_file:
        log_file = f'{args.logging_file}.txt'
    elif args.verbosity or args.test:
        log_file = f'bot_logging_test_{datetime.date.today().strftime("%d_%m_%y")}.txt'  # if -t then log into separate file
    else:
        log_file = 'bot_logging.txt'
    try:
        with open(log_file, 'a') as file:
            x = datetime.datetime.now()
            file.write(f'{x.strftime("%d %b %Y  %H:%M:%S")}  -  {log_message}\n')
    except FileNotFoundError:
        with open(log_file, 'w+') as file:
            pass
        log(log_message)
    return True


if __name__ == '__main__':
    # Parsing command-line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('-t', '--test', action='store_true',
                        help='Runs in a test mode and won\'t make any modifications. Also logs to a separate log file (unless -l is specified). Implies -v.')
    parser.add_argument('-l', '--logging-file',
                        help='Specifies a separate logging file. If it doesn\' exist, it will be created. If it exists, additional logs will be appended to it.')
    parser.add_argument('-s', '--submission',
                        help='Runs only one test moderation cycle against a submission specified by it\'s ID.')
    parser.add_argument('-S', '--submission-test', help='Same as -s with automatic test mode -t (and -v logging)')
    parser.add_argument('-v', '--verbosity', action='store_true',
                        help='Makes the program log more stuff. Useful only for debugging/testing.')
    parser.add_argument('-r', '--subreddit-name', help='Specifies a subreddit to run on. Default = r/amv.',
                        default='amv')
    args = parser.parse_args()

    if args.submission_test:
        args.submission = args.submission_test
        args.test = True

    # Log bootup message here because logging file depends on args.test that might be changed above by args.submission_test
    log(f'\n'
        f'********************************************'
        f'              STARTING UP                   '
        f'********************************************'
        f'\n')

    if args.test:
        args.verbosity = True
        log(f'Warning: Running in test mode! There will be no changes done to the subreddit!')
    if args.logging_file:
        log(f'Logs will be save to file {args.logging_file}.txt')
    if args.submission:
        log(f'Running only one moderation loop against submission ID {args.submission}')

    times_crashed = 0
    reddit = initialize_reddit()
    subreddit = reddit.subreddit(args.subreddit_name)
    if args.verbosity:
        log(f'Subreddit {args.subreddit_name} initialized.')

    timer = int(time.time())
    if args.submission:
        submission = initialize_reddit().submission(id=args.submission)
        try:
            submission.title
        except:
            log(f'Wrong ID {args.submission} passed as argument to option -s (-submission).')
            sys.exit(1)
        regular_moderation(submission)
        if args.verbosity:
            log('Shutting down due to -s option...')
        sys.exit(0)

    # Running the main loop, restarts itself up to two times before collapsing in blood on the floor for good.

    while True:
        try:
            for submission in subreddit.stream.submissions():
                regular_moderation(submission)
        except KeyboardInterrupt:
            log('Shutting down...')
            break
        except Exception as e:
            times_crashed += 1
            if times_crashed <= 2:
                log(f'Crashed because of a following error: {e}.')
                log(f'Will try to restart in 5 minutes')
                time.sleep(300)
                log('Restarting...')
                continue
            else:
                log(f'Crashed because of a following error: {e}.')
                log(f'Automatic restart disabled because program has crashed {times_crashed} times since last manual check.')
                admin = reddit.redditor('Zbynasuper')
                admin.message('MasterEditor has crashed.', 'Hey, your awesome bot has crashed for some reason. Check me out plz.')
                break
