import json
import os
import subprocess
import re
import funcy


from analysis.dev import DATA_DIR, GIT_LOG_FORMAT, DELIM
from analysis.parsing_regex import JS_IPFS_REGEX_DICT, GO_ETHEREUM_REGEX_DICT

from analysis.test_parselog import run_loc_test, count_png_errors, check_test_dict
from analysis.test_data import TEST_COM10, TEST_COM1000, TEST_GOETH_2016, TEST_GOETH_2017, TEST_GOETH_2018
from analysis.helpers import serialize_to_dict


from analysis.objects import Commit, Commit_info, Statistic, FileChange
# this is max characters at the end of a regex
MAX_STATS_LEN = 10
MAX_FILE_EXT_LEN = 3
# for js-ipfs, this value should be 6 MAX_FILE_EXT_LEN = 6
# for go-etherem, hashes are 40 long and git id hash abbrev is 9
# for jsipfs the sha1 hash is 32 long and the git id is 7 long


def is_match(reg, line):
    # search will look for match over the entire string, match will onlyu look for matches starting at the beginning
    m = re.search(reg, line)
    return bool(m is not None)


def as_list(elements):
    items = [e for e in elements if e]
    return items or None


def fix_parents(parents_string):
    return as_list(parents_string.split())


def fix_refs(refs_string):
    return as_list(refs_string.strip()[1:-1].split(', '))


def make_cmd(git_log_format=GIT_LOG_FORMAT, start=None, end=None, no_renames=False, no_merges=False, show_diff=True,
             branch=None, file_extensions=None):
    '''
    start and end dates should be in YYYY-MM-DD str format
    also file extensions should be of the form '\"*.go\"'  with the double quotes of they won't work as well
    for example ' "*.go" "*.md" '

    '''
    git_log = ['git', 'log', '--all', '--numstat', '--format=format:{0}'.format(git_log_format)]
    if start is not None:
        git_log.append('--since')
        git_log.append(start)
    if end is not None:
        git_log.append('--until')
        git_log.append(end)
    if no_renames:
        git_log.append('--no-renames')
    if no_merges:
        git_log.append('--no-merges')
    if show_diff:
        git_log.append('--patch')
    if branch is not None:
        git_log.append(str(branch))
    if file_extensions is not None:
        git_log.append(file_extensions)
    return ' '.join(git_log)


def process_lines(line, regex_dict):
    # most of the unicode decode errors/stats error caused by config files
    try:
        if not isinstance(line, str):
            line = line.decode('utf-8')
    except UnicodeDecodeError as e:
        print('Unicode error ', e)
        print(line)
        return ('error', line)
    # turn bytes object into a string
    if is_blank(line):
        return (None, None)
    if is_commit(line, regex_dict['commit_reg']):
        print('IS COMMIT')
        return ('commit', make_commit(line))
    try:
        if is_stats(line, regex_dict['stats_reg']):
            print('IS STAT')
            return ('statistic', make_stats(line, regex_dict['stats_reg']))
    except TypeError:
        print(line)
        return ('error', line)
        # if not stat or not a commit line, must be blank or a diff
    return ('diff', make_diff(line))


def yield_gitlog_lines(repo_path, **kwargs):
    command = make_cmd(**kwargs)
    print('command ', command)
    # shell = True indicates you are passing a string
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, cwd=repo_path, shell=True)
    for line in proc.stdout:
        yield line.strip()


# for processing text with diffs, I need to group commit, statistics and patch together in one group identified by the hash
# and report back
# I need some type of iterator that goes throuhgh line by line

def process_list_git_objs(lst):
    '''
    Input is a list of git commit_info objects, Commit_info or Staticts Or Diffs
    The goal is to group all the stats and diffs to one commit object
    list should be a list of tuples (string_identifier, object)
    I also need to work on not keeping track of config or boilerplat files, because these tend to mess up the parser
    '''
    errors = []
    commit_objs = []
    curr_commit = None
    for item_name, item_obj in lst:
        try:
            if item_name == 'commit':
                # append old curr commit
                if curr_commit is not None:
                 commit_objs.append(curr_commit)
                # create a new commit obj to add to
                curr_commit = Commit(item_obj)
            elif item_name == 'error':
                errors.append(item_obj)
            elif item_name == 'statistic':
                curr_commit.statistics_list.append(item_obj)
            elif item_name == 'diff':
                curr_commit.diffs_list.append(item_obj)
            elif item_name is None:
                # this is a blank line
                continue
        except Exception as e:
            print(item_name)
            print(item_obj)
            continue
    # after loop, append last commit object and return list of obj
    commit_objs.append(curr_commit)
    return commit_objs, errors


def is_commit(line, commit_reg):
    # js-ipfs commit_reg = r'[a-f0-9]{32}' + delim + '[a-f0-9]{7}' + delim + '[a-f0-9]{32}'
    # go-ether commit_reg = r'[a-f0-9]{40}' + delim + '[a-f0-9]{9}' + delim + '[a-f0-9]{40}'

    return is_match(commit_reg, line)


def make_commit(line):
    #     commit_line = re.sub(r'----',' ', commit_line.decode('utf-8'))
    comm_args = line.split(DELIM)
    comm_args.append(line)
    commit = Commit_info(*comm_args)
    parents = fix_parents(commit.parents)
    return commit._replace(parents=parents)


def is_stats(line, stats_reg):
    # note that commit spacing isnt exactly a tab, it is greater than that so need to have the multiple spaces
    #stats_reg = r'(\d{1,{}'.format(MAX_STATS_LEN)+'})(\s+)(\d{1,{}'.format(
    #    MAX_STATS_LEN)+ '(\s+)([a-zA-Z/]+)(\.)(\w{1,{}'.format(MAX_FILE_EXT_LEN)})'
    return is_match(stats_reg, line)


def make_stats(line, stats_reg):
    # NOTE THAT YOU CANNOT HAVE VARS IN REGEX STR
    # this needs to match regex
    g = re.match(stats_reg, line)
    return Statistic(*g.groups())


def make_diff(line):
    return line


def is_blank(line):
    return bool(line is None or line is '')


def is_index(line, index_reg, new_index_reg):
    '''Check for an Index line like: 'index 582ba6e..0cd52f1 100644'
    '''

    m = is_match(index_reg, line)
    if m:
        return True
    # for new files different index syntax
    return is_match(new_index_reg, line)


def parse_filetype(line, index_reg):
    m = re.search(index_reg, line)
    if m:
        return m.groups()[-1]
    # try new file match - no file type
    return None


def check_start_diff(line, start_diff_reg):
    '''
    Check for the start of a file changed in the diff output
    '''
    return is_match(start_diff_reg, line)


def parse_filename(line, start_diff_reg):
    '''
    Parse Filename changed from start of the diff
    '''
    m = re.search(start_diff_reg, line)
    names = list(filter(None, m.groups()))
    # note the filenames should be identical except in the case of a rename
    return names[1]


def is_location_filename_info(line, location_reg):
    '''
    The format is the @@ from-file-range to-file-range @@ [header].
     The from-file-range is in the form -<start line>,<number of lines>,
     and to-file-range is +<start line>,<number of lines>.
     Both start-line and number-of-lines refer to position and length of hunk in preimage and postimage, respectively.
     If number-of-lines not shown it means that it is 0.
     Sample: '@@ -15,27 +15,33 @@ module.exports = function libp2p (self) {'
    '''
    return is_match(location_reg, line)


def parse_location_filename_info(line, location_reg):
    '''Pull out location of code change and function name if available'''
    m = re.search(location_reg, line)
    g = m.groups()
    # this should be start, num changed to file is startline, num-lines
    if len(g[-1]) > 0 and re.match(r'\D+', g[-1]) is not None:
        return g[:-1], g[-1]
    return g[:-1], None


def is_filenames_changed(line, filename_reg, devnull_file_reg):
    '''
    Check for preimage and post image filenames
    Samples:
    '--- a/src/core/components/libp2p.js',
    '+++ b/src/core/components/libp2p.js'
    Note that new files will have /dev/null as the old filename
    '''
    filenames = is_match(filename_reg, line)
    if filenames:
        return True
    new = is_match(devnull_file_reg, line)
    return new


def get_filenames_changed(line, filename_reg, devnull_file_reg):
    '''
    Extract Filename
    Samples:
    '--- a/src/core/components/libp2p.js',
    '+++ b/src/core/components/libp2p.js'
    '''
    # first check for filenames of newly created files
    new_file_reg, old_file_reg = devnull_file_reg.split('|')
    m = re.search(new_file_reg, line)
    if m is not None:
        return '/dev/null', None
    m = re.search(old_file_reg, line)
    if m is not None:
        print(m.groups())
        return None, '/dev/null'
    m = re.search(filename_reg, line)
    g = m.groups()
    return g


def is_raw_diff(line, diff_line_reg):
    return is_match(diff_line_reg, line)


def is_rename(line):
    '''renames start with a special function under the function name
     'similarity index 68%',
     'rename from src/core/namesys/index.js',
     'rename to src/core/ipns/index.js'
     '''
    return (line.startswith('similarity index') or line.startswith('rename '))


def is_deletion(line):
    '''
    Deletions start with 'deleted file'
    '''
    return (line.startswith('deleted file'))


def is_new_file(line):
    '''
    Create files have a special line 'new file mode
    '''
    return (line.startswith('new file mode'))


def make_file_obj(change_dict, last_change_text):
    '''wrapper to make FileChange Obj'''
    if len(last_change_text) > 0:
        change_dict['list_changes'].append(last_change_text)
    return FileChange(**change_dict)


def create_file_obj_dict(namedtup):
    # create fields with all the info we are saving from the diff text
    change_dict = dict(zip(namedtup.__dict__['_fields'], funcy.repeat(None)))
    change_dict['raw_diff'] = []
    change_dict['filename_old'] = ''
    change_dict['filename_new'] = ''
    change_dict['functions_changed'] = []
    change_dict['locations_changed'] = []
    change_dict['list_changes'] = []
    change_dict['num_changes'] = 0
    change_dict['is_rename'] = False
    change_dict['is_new'] = False
    change_dict['is_deletion'] = False

    return change_dict


def parse_diff_text(lst_diff_lines, regex_dict):
    '''
    Parse the Output of the diff into a list of changes by line and code changed
    One diff text is made up of multiple file changes
    Each file changed is noted by a line as follows:
    'diff --git a/src/core/components/libp2p.js b/src/core/components/libp2p.js'
    "git diff" header in the form diff --git a/file1 b/file2.
    the --git indicates the diff is in git form
    The a/ and b/ filenames are the same unless rename/copy is involved (which we have excluded)
    Next lines is an index line:
    'index 582ba6e..0cd52f1 100644'
    100644 means that it is ordinary file and not e.g. symlink, and that it doesn't have executable permission bit),
    The index bit indicates a  shortened hash of preimage (the version of file before given change) and postimage
    (the version of file after change)
    The next two lines:
     '--- a/src/core/components/libp2p.js',
     '+++ b/src/core/components/libp2p.js'
    are the source (preimage) and destination (postimage) file names.
    If file was created the source is /dev/null; if file was deleted, the target is /dev/null
    The next lines shows where the changes occured and name of function where it occurred
    '@@ -15,27 +15,33 @@ module.exports = function libp2p (self) {'
     The format is the @@ from-file-range to-file-range @@ [header].
     The from-file-range is in the form -<start line>,<number of lines>,
     and to-file-range is +<start line>,<number of lines>.
     Both start-line and number-of-lines refer to position and length of hunk in preimage and postimage, respectively.
     If number-of-lines not shown it means that it is 0.

     Note that one diff can have multiple location change lines, each with a text diff below

     Next comes the description of where files differ.
     The lines common to both files begin with a space character.
     The lines that actually differ between the two files have one of the following indicator
     characters in the left print column:

    '+' -- A line was added here to the first file.
    '-' -- A line was removed here from the first file.
    https://stackoverflow.com/questions/2529441/how-to-read-the-output-from-git-diff
    '''
    change_objs = []

    curr_file = None
    change_dict = create_file_obj_dict(FileChange)
    curr_changes = []

    for line in lst_diff_lines:

        start = check_start_diff(line, regex_dict['start_diff_reg'])
        if is_blank(line):
            continue

        elif start:
            # check if curr_obj is none, if not append to list of change objects
            if curr_file is not None:
                # decide how to make objc
                curr_obj = make_file_obj(change_dict, curr_changes)
                change_objs.append(curr_obj)
            curr_file = parse_filename(line, regex_dict['start_diff_reg'])
            change_dict = create_file_obj_dict(FileChange)
            curr_changes = []
            change_dict['raw_diff'].append(line)

        elif is_index(line, regex_dict['index_reg'], regex_dict['new_index_reg']):
            change_dict['filetype'] = parse_filetype(line, regex_dict['index_reg'])
            change_dict['raw_diff'].append(line)

        elif is_rename(line):
            # rename diffs have a segment of text
            change_dict['is_rename'] = True
            change_dict['raw_diff'].append(line)

        elif is_new_file(line):
            # newly created files have a line indicating that new file mode
            change_dict['is_new'] = True
            change_dict['raw_diff'].append(line)

        elif is_deletion(line):
            # newly created files have a line indicating that new file mode
            change_dict['is_deletion'] = True
            change_dict['raw_diff'].append(line)

        elif is_filenames_changed(line, regex_dict['filename_reg'], regex_dict['devnull_file_reg']):
            # keep track of files that changed
            l = get_filenames_changed(line, regex_dict['filename_reg'], regex_dict['devnull_file_reg'])
            # if a filename is in the first group, it is the old filename
            # if it is in the second position, it is the new filename
            change_dict['raw_diff'].append(line)
            if l[0] is None:
                change_dict['filename_new'] = l[1]
            elif l[1] is None:
                change_dict['filename_old'] = l[0]
            else:
                print('Error in filename parsing ', l, line)

        elif is_location_filename_info(line, regex_dict['location_reg']):
            # in this, we need to add function name changed to list, add location changed to list
            # and then start a new change text block to append the raw text to
            change_dict['raw_diff'].append(line)
            loc_info, funcname = parse_location_filename_info(line, regex_dict['location_reg'])
            change_dict['locations_changed'].append(loc_info)
            change_dict['functions_changed'].append(funcname)
            # increment number of changes
            change_dict['num_changes'] += 1
            # create a new block of text for list changes and append
            if len(curr_changes) > 0:
                change_dict['list_changes'].append(curr_changes)
            curr_changes = []

        elif is_raw_diff(line, regex_dict['diff_line_reg']):
            # get last entry in change dict and append line
            curr_changes.append(line)
            change_dict['raw_diff'].append(line)

        else:
            print('LINE NOT CAPTURED ', line)

    # add last created object to list of current objects and return
    curr_obj = make_file_obj(change_dict, curr_changes)
    change_objs.append(curr_obj)
    return change_objs


def gen_parse_log(repo_path, regex_dict, **kwargs):
    # need the list to force execution
    cs = map(lambda x: process_lines(x, regex_dict), yield_gitlog_lines(repo_path, **kwargs))
    commits, errors = process_list_git_objs(cs)
    for com in commits:
        try:
            diff_objs = parse_diff_text(com.diffs_list, regex_dict)
            com.diff_objs_list = diff_objs
        except AttributeError as e:
            print(e)
            print(com.__dict__)
    return commits


def gen_save_commitlog_jsipfs(repo_path, saving_to_fname, **kwargs):
    commits = gen_parse_log(repo_path, JS_IPFS_REGEX_DICT, **kwargs)
    errors = run_loc_test(commits)
    count_png_errors(errors)
    # select commit
    for test_dict in [TEST_COM1000, TEST_COM10]:
        to_test = list(filter(lambda x: x.commit_info.sha1 == test_dict['sha1'], commits))[0]
        check_test_dict(to_test, test_dict)

    coms = list(map(serialize_to_dict, commits))

    print('Saving to ', os.path.join(DATA_DIR, saving_to_fname))
    with open(os.path.join(DATA_DIR, saving_to_fname), 'w') as f:
        json.dump(coms, f)


def gen_save_commitlog_goethereum(repo_path, saving_to_fname, **kwargs):
    commits = gen_parse_log(repo_path, GO_ETHEREUM_REGEX_DICT, **kwargs)
    errors = run_loc_test(commits)
    # save files to disk before checking for errors to avoid wasting time
    coms = list(map(serialize_to_dict, commits))

    print('Saving to ', os.path.join(DATA_DIR, saving_to_fname))
    with open(os.path.join(DATA_DIR, saving_to_fname), 'w') as f:
        json.dump(coms, f)

    # select commit - want to make these tests go ethereum specific
    try:
        for test_dict in [TEST_GOETH_2017, TEST_GOETH_2018, TEST_GOETH_2016]:
            print('Testing ', test_dict['sha1'])
            to_test = list(filter(lambda x: x.commit_info.sha1 == test_dict['sha1'], commits))[0]
            check_test_dict(to_test, test_dict)
        count_png_errors(errors)
    except IndexError as e:
        print(e)
        print(len(errors))
        print(errors[0])
    return commits, errors
