#!/usr/bin/env python
# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla Corporation Code.
#
# The Initial Developer of the Original Code is
#
#  Sam Liu <sam@ambushnetworks.com>
#
# Portions created by the Initial Developer are Copyright (C) 2009
# the Initial Developer. All Rights Reserved.
#
# Contributor(s): Sam Liu <sam@ambushnetworks.com>
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****


'''
Known Issues:
    1) Multi-core compilation on Windows not supported
    2) Won't work on Windows 2000 or windows where home directory
       has spaces -- if we use ~ the build command will fail.
'''

from optparse import OptionParser, OptionGroup #note: deprecated in Python27, use argparse
from mozrunner import Runner, FirefoxRunner
import simplejson, urllib
import ximport
from types import *
from utils import hgId, captureStdout, increment_day
import os, sys, subprocess, string, re, tempfile, shlex, glob, shutil, datetime, multiprocessing
from time import gmtime, strftime

#Global Variables
showMakeData = 0
progVersion="0.4.5"

class Builder():
    def __init__(self, makeCommand=["make","-f","client.mk","build"] , shellCacheDir=os.path.join(os.path.expanduser("~"), "moz-commitbuilder-cache"), cores=1, repoURL="http://hg.mozilla.org/mozilla-central",clean=False, mozconf=None):
        #Set variables that we need
        self.makeCommand = makeCommand
        self.shellCacheDir = shellCacheDir
        self.cores = cores
        self.repoURL = repoURL
        self.confDir = os.path.join(shellCacheDir, "mozconf")
        self.repoPath = os.path.join(shellCacheDir,"mozbuild-trunk")
        self.hgPrefix = ['hg', '-R', self.repoPath]
        self.mozconf = mozconf
        self.objdir = os.path.join(self.repoPath,"obj-ff-dbg")

        #Create directories we need
        if not os.path.exists(shellCacheDir):
            os.mkdir(shellCacheDir)
        if not os.path.exists(self.confDir):
            os.mkdir(self.confDir)

        #Sanity check: make sure hg is installed on the system, otherwise do not proceed!
        try:
            testhgInstall = subprocess.Popen(["hg","--version"],stdout=subprocess.PIPE)
            testresult = testhgInstall.communicate()
        except OSError as err:
            print "hg not installed on this system."
            quit()

        #Download a local trunk.
        self.getTrunk(makeClean=clean)

    def getTip(self):
        #Get trunk's tip changeset identifier hash
        try:
            tiprev = subprocess.Popen(self.hgPrefix+["tip"],stdout=subprocess.PIPE)
            capturedString = tiprev.communicate()
            try:
                changesetTip = capturedString[0].split("\n")[0].split(" ")[3].split(":")[1]
            except:
                pass
        except:
            print "Woops, something went wrong!"
            quit()

        #Ensure that changesetTip actually contains something
        if changesetTip:
            return changesetTip
        else:
            print "Couldn't get the tip changeset."

    def changesetFromDay(self, date, oldest=True):
        # Gets first changeset from a given date via pushlog
        nextDate = increment_day(date)
        pushlog_url = "http://hg.mozilla.org/mozilla-central/json-pushes?startdate="+date+"&enddate="+nextDate
        pushlog_json = simplejson.load(urllib.urlopen(pushlog_url))
        sorted_keys = sorted(map(int,pushlog_json.keys()))

        changesetString = None
        if oldest:
           try:
               pushlog_first = str(sorted_keys.pop(0))
               changesetString = str(pushlog_json[pushlog_first]['changesets'][0])
           except:
              pass
        else:
           try:
              pushlog_last = str(sorted_keys.pop(-1))
              changesetString = str(pushlog_json[pushlog_last]['changesets'][0])
           except:
                   pass

        if changesetString != None:
            return changesetString

        return False

    def getTrunk(self, makeClean=False):
        #Get or update local trunk -- makeclean means delete old trunk and fetch fresh
        if makeClean:
            print "Making clean trunk environment..."
            try:
                shutil.rmtree(self.repoPath)
            except:
                pass
        #Gets or updates our cached repo for building
        if os.path.exists(os.path.join(self.repoPath,".hg")):
            print "Found a recent trunk. Updating it to head before we begin..."
            updateTrunk = subprocess.call(self.hgPrefix + ["pull","-u"])
            print "Update successful."
        else:
            print "Trunk not found."
            makeClean = subprocess.call(["rm","-rf", self.repoPath])
            print "Removed old mozbuild-trunk directory. Downloading a fresh repo from mozilla-central..."
            #downloadTrunk = os.popen("hg clone http://hg.mozilla.org/mozilla-central mozbuild-trunk")
            downloadTrunk = subprocess.call(["hg", "clone", self.repoURL, "mozbuild-trunk"], cwd=self.shellCacheDir)

    def mozconfigure(self):
        #Set mozconfig settings
        print "\nConfiguring mozconfig:"

        #Make a place to put our mozconfig
        mozconfig_path = os.path.join(self.confDir, 'config-default')
        if os.path.exists(mozconfig_path):
            os.unlink(mozconfig_path)

        #Using external mozconfig
        if self.mozconf:
            #Copy their mozconfig, but use our own objdir param
            #Also cores. Don't break the flag's functionality!
            shutil.copy(self.mozconf,mozconfig_path)
            f=open(mozconfig_path, 'a')
            f.write('mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-ff-dbg\n')
            if sys.platform != "win32" or sys.platform != "cygwin":
                f.write('mk_add_options MOZ_MAKE_FLAGS="-s -j '+str(self.cores)+'"')
            os.environ['MOZCONFIG']=mozconfig_path
            return

        #Using our custom mozconfig
        f=open(mozconfig_path, 'w')
        f.write('mk_add_options MOZ_OBJDIR=@TOPSRCDIR@/obj-ff-dbg\n')

        #According to Jesse, these are the fastest (build-wise) options
        f.write('ac_add_options --disable-optimize\n')
        f.write('ac_add_options --enable-debug\n')
        f.write('ac_add_options --enable-tests\n')

        if sys.platform == "win32" or sys.platform == "cygwin":
            print "Windows detected, multicore support disabled. Compiling with 1 core..."
            f.write("ac_add_options --with-windows-version=600\n")
            f.write("ac_add_options --enable-application=browser\n")
        else:
            print "Compiling with "+str(self.cores)+ " cores.\n"
            f.write('mk_add_options MOZ_MAKE_FLAGS="-s -j '+str(self.cores)+'"')

        f.close()

        #export MOZCONFIG=/path/to/mozilla/mozconfig-firefox
        os.environ['MOZCONFIG']=mozconfig_path

    def importAndBisect(self, good, bad, testcondition=None, args_for_condition=[]):
        #Convenience function for API use only
        conditionscript = ximport.importRelativeOrAbsolute(testcondition)
        self.bisect(good,bad, testcondition=conditionscript, args_for_condition=args_for_condition)

    def bisect(self,good,bad, testcondition=None, args_for_condition=[]):
        #Call hg bisect with initial params, set up building environment (mozconfig)

        #Support for using dates
        badDate = re.search(r'(\d\d\d\d-\d\d-\d\d)',good)
        goodDate = re.search(r'(\d\d\d\d-\d\d-\d\d)',bad)
        if badDate != None and goodDate != None:
            badDate = badDate.group(1)
            goodDate = goodDate.group(1)
            good = self.changesetFromDay(goodDate) #Get first changeset from this day
            bad = self.changesetFromDay(badDate, oldest=False) #Get last changeset from that day

            if not (bad and good):
                print "Invalid date range."
                quit()

            #Since they entered dates, lets give them info about the changeset range
            print "Bisecting on changeset range " + str(good)[:12] + " to " + str(bad)[:12]

        #Get the actual changesets that we will be using
        good = hgId(good, self.hgPrefix)
        bad = hgId(bad, self.hgPrefix)
        print good
        print bad
        print self.validate(good, bad)

        if good and bad and self.validate(good, bad):
            #Valid changesets, so do the bisection
            subprocess.call(self.hgPrefix+["bisect","--reset"])

            setUpdate = captureStdout(self.hgPrefix+["up",bad])
            setBad = captureStdout(self.hgPrefix+["bisect","--bad"])
            setGood = captureStdout(self.hgPrefix+["bisect","--good",good])

            print str(setUpdate)
            print str(setBad)
            print str(setGood)

            self.check_done(setGood)

            # Set mozconfig
            self.mozconfigure()

            # Call recursive bisection!
            self.bisectRecurse(testcondition=testcondition, args_for_condition=args_for_condition)

        else:
            print "Invalid values. Please check your changeset revision numbers."


    def check_done(self, doneString):
       # Check if we should terminate early because the bisector exited?
        string_to_parse = str(doneString)

        branch_unaware_flag = string_to_parse.find("Not all ancestors")
        traceback_flag = string_to_parse.find("--extend")
        regression_found = string_to_parse.find("The first")

        if traceback_flag > -1:
            #hg 1.9 and up only has --extend, which is branch aware
            print "Using hg 1.9, we're branch aware! Let's explore that ancestor branch..."
            subprocess.call(self.hgPrefix+["bisect","--extend"])
        elif branch_unaware_flag > -1:
            print "Not using hg 1.9 (not automatic) so you need to bisect again with the above changeset."
        elif regression_found > -1:
            print "Regression found using mozcommitbuilder " + progVersion + " on " + sys.platform + " at " + strftime("%Y-%m-%d %H:%M:%S", gmtime())
            quit()

    def bisectRecurse(self, testcondition=None, args_for_condition=[]):
        #Recursively build, run, and prompt
        verdict = ""

        try:
            self.build()
        except Exception:
            print "This build failed!"
            verdict = "skip"

        if verdict == "skip":
            pass
        elif testcondition==None:
            #Not using a test, interactive bisect begin!
            self.run()
        else:
            #Using Jesse's idea: import any testing script and run it as the truth condition
            args_to_pass = [self.objdir] + args_for_condition

            if hasattr(testcondition, "init"):
                testcondition.init(args_to_pass)

            #TODO: refactor to use directories with revision numbers
            tmpdir = tempfile.mkdtemp()
            verdict = testcondition.interesting(args_to_pass,tmpdir)

            #Allow user to return true/false or bad/good
            if verdict != "bad" and verdict != "good":
                verdict = "bad" if verdict else "good"

        while verdict not in ["good", "bad", "skip"]:
            verdict = raw_input("Was this commit good or bad? (type 'good', 'bad', or 'skip'): ")
            if verdict == 'g':
                verdict = "good"
            if verdict == 'b':
                verdict = "bad"
            if verdict == 's':
                verdict = "skip"

        # do hg bisect --good, --bad, or --skip
        verdictCommand = self.hgPrefix+["bisect","--"+verdict]
        print " ".join(verdictCommand)
        retval = captureStdout(verdictCommand)

        string_to_parse = str(retval)
        print string_to_parse

        self.check_done(string_to_parse)

        if retval.startswith("Testing changeset"):
            print "\n"

        self.bisectRecurse(testcondition=testcondition, args_for_condition=args_for_condition)

    def buildAndRun(self, changeset=0):
        #API convenience function
        self.build(changeset=changeset)
        #print "Starting up Firefox..."
        self.run()
        #print "Complete! Firefox should be running."

    def build(self, changeset=0):
        #Build a binary and return the file path
        #Binary file named by changeset number
        if changeset != 0:
            changeset = str(changeset)
            print "Switching to revision "+changeset[:8]+"..."
            subprocess.call(self.hgPrefix+["update",changeset]) #switch to a given directory

        #Call make on our cached trunk
        print "Building..."
        makeData = captureStdout(self.makeCommand, ignoreStderr=True,
                                                        currWorkingDir=self.repoPath)
        if showMakeData == 1:
            print makeData

        print "Build complete!"

    def run(self):
        #Run the built binary if it exists. ONLY WORKS IF BUILD WAS CALLED!
        if sys.platform == "darwin":
            runner = FirefoxRunner(binary=os.path.join(self.shellCacheDir,"mozbuild-trunk","obj-ff-dbg","dist","NightlyDebug.app","Contents","MacOS")+"/firefox-bin")
            runner.start()
            runner.wait()
        elif sys.platform == "linux2":
            runner = FirefoxRunner(binary=os.path.join(self.shellCacheDir,"mozbuild-trunk","obj-ff-dbg","dist","bin") + "/firefox")
            runner.start()
            runner.wait()
        elif sys.platform == "win32" or sys.platform == "cygwin":
            runner = FirefoxRunner(binary=os.path.join(self.shellCacheDir,"mozbuild-trunk","obj-ff-dbg","dist","bin") + "/firefox.exe")
            runner.start()
            runner.wait()
        else:
            print "Your platform is not currently supported."
            quit()

    def getBinary(self, revision):
        #Create binary, put into cache directory.
        #Returns path of binary for a given changeset
        self.build(changeset=revision)

        #Run "make package"
        print "Making binary..."
        makeData = captureStdout(["make","package"], ignoreStderr=True,
                                 currWorkingDir=os.path.join(self.repoPath,"obj-ff-dbg"))

        binary = None
        renamedBinary = None
        distFolder = os.path.join(self.repoPath,"obj-ff-dbg","dist")
        #return path to package
        if sys.platform == "darwin":
            try:
                binary = glob.glob(distFolder+"/firefox-*.dmg")[0]
            except:
                binary = None
            renamedBinary = str(revision[:8]) + ".dmg" #Don't want the filename to be too long :)
        elif sys.platform == "linux2":
            try:
                binary = glob.glob(distFolder+"/firefox-*.tar.gz")[0]
            except:
                binary = None
            renamedBinary = str(revision[:8]) + ".tar.gz"
        elif sys.platform == "win32" or sys.platform == "cygwin":
            try:
                binary = glob.glob(distFolder+"/firefox-*.zip")[0]
            except:
                binary = None
            renamedBinary = str(revision[:8]) + ".zip"
        else:
            print "ERROR: This platform is unsupported."
            quit()

        if binary != None:
            print "Binary build successful!"
            print renamedBinary + " is the binary:"

            #Move the binary into the correct place.
            try:
                os.remove(os.path.join(self.shellCacheDir,renamedBinary))
            except:
                pass
            shutil.move(binary, os.path.join(self.shellCacheDir,"builds",renamedBinary))

            #Return binary path
            return (os.path.join(self.shellCacheDir,"builds",renamedBinary), renamedBinary)

        print "ERROR: Binary not found."
        quit()

    def validate(self, good, bad):
        #Check that given changeset numbers aren't wonky
        if (good == bad):
            return False
        return True

def cpuCount():
    try:
        return multiprocessing.cpu_count()
    except NotImplementedError:
        return 1

def cli():
    #Command line interface
    usage = """usage: %prog --good=[changeset] --bad=[changeset] [options] \n       %prog --single=[changeset] -e
            """

    parser = OptionParser(usage=usage,version="%prog "+progVersion)
    parser.disable_interspersed_args()

    group1 = OptionGroup(parser, "Global Options",
                                        "")
    group1.add_option("-j", "--cores", dest="cores", default=cpuCount(),
                                        help="Max simultaneous make jobs (default: %default, the number of cores on this system)",
                                        metavar="[numjobs]")
    group1.add_option("-m", "--mozconfig", dest="mozconf",
                                        help="external mozconfig if so desired",
                                        metavar="[mozconf path]", default=False)

    group1.add_option("-f", "--freshtrunk", action = "store_true", dest="makeClean", default=False,
                                        help="Delete old trunk and use a fresh one")


    group2 = OptionGroup(parser, "Bisector Options",
                                        "These are options for bisecting on changesets. Dates are retrieved from pushlog " \
                                        "and are not as reliable as exact changeset numbers.")
    group2.add_option("-g", "--good", dest="good",
                                        help="Last known good revision",
                                        metavar="[changeset or date]")
    group2.add_option("-b", "--bad", dest="bad",
                                        help="Broken commit revision",
                                        metavar="[changeset or date]")

    group3 = OptionGroup(parser, "Single Changeset Options",
                                        "These are options for building a single changeset")

    group3.add_option("-s", "--single", dest="single",
                                        help="Build a single changeset",
                                        metavar="[changeset]")

    group3.add_option("-e", "--run", action="store_true", dest="run", default=False,
                                        help="run a single changeset")

    group4 = OptionGroup(parser, "Binary building options",
                                        "These are options for building binaries from a single changeset")

    group4.add_option("-x", "--binary", action="store_true", dest="binary", default=False,
                                        help="build binary and return path to it")

    group4.add_option("-q", "--revision", dest="revision", default=None, metavar="[changeset]",
                                        help="revision number for single changeset to build binary from")

    group5 = OptionGroup(parser, "Automatic Testing Options (EXPERIMENTAL)",
                                        "Options for using an automated test instead of interactive prompting for bisection. " \
                                        "Please read documentation on how to write testing functions for this script.")

    group5.add_option("-c", "--condition", dest="condition", default=None, metavar="[condtest.py -opt1 -opt2]",
                                        help="External condition for bisecting. " \
                                             "Note: THIS MUST BE THE LAST OPTION CALLED.")

    group6 = OptionGroup(parser, "Broken and Unstable Options",
                                        "Caution: use these options at your own risk.  "
                                        "They aren't recommended.")

    group6.add_option("-R", "--repo", dest="repoURL",
                                        help="alternative mercurial repo to bisect NOTE: NEVER BEEN TESTED",
                                        metavar="valid repository url")


    parser.add_option_group(group1)
    parser.add_option_group(group2)
    parser.add_option_group(group3)
    parser.add_option_group(group4)
    parser.add_option_group(group5)
    parser.add_option_group(group6)
    (options, args_for_condition) = parser.parse_args()

    # If a user only wants to make clean or has supplied no options:
    if (not options.good or not options.bad) and not options.single:
        if options.makeClean:
            #Make a clean trunk and quit.
            commitBuilder = Builder(clean=options.makeClean)
        else:
            # Not enough relevant parameters were given
            # Print a help message and quit.
            print """Use -h flag for available options."""
            print """To bisect, you must specify both a good and a bad date. (-g and -b flags are not optional)."""
            print """You can also use the --single=[chset] flag to build and --run to run a single changeset."""
        quit()

    # Set up a trunk for either bisection or build.
    mozConfiguration = options.mozconf
    commitBuilder = Builder(clean=options.makeClean, mozconf=mozConfiguration)
    if options.cores:
        commitBuilder.cores = options.cores
        commitBuilder.mozconfigure()

    # TODO: Allow a user to build a binary from cli
    if options.binary:
        pass
        #if options.revision:
        #    pass
        #else:
        #    print "You need to specify a revision to build the binary from."

    # For building single commits:
    if options.single:
        if options.run:
            commitBuilder.buildAndRun(changeset=options.single)
            print "Firefox successfully built and ran!"
        else:
            commitBuilder.build(changeset=options.single)
            print "Local trunk built. Not running."

    # For bisections:
    elif options.good and options.bad:
        print "Begin interactive commit bisect!\n"

        if options.repoURL:
            commitBuilder.repoURL = options.repoURL
            print "Using alternative repository "+options.repoURL

        conditionscript = None
        if options.condition:
            conditionscript = ximport.importRelativeOrAbsolute(options.condition)
                                                               /opt/local/Library/Frameworks/Python.framework/Versions/2.6/bin/mozcommitbuilder:5: UserWarning: Module pkg_resources was already imported from /opt/local/Library/Frameworks/Python.framework/Versions/2.6/lib/python2.6/site-packages/setuptools-0.6c11-py2.6.egg/pkg_resources.py, but /opt/local/Library/Frameworks/Python.framework/Versions/2.6/lib/python2.6/site-packages/distribute-0.6.16-py2.6.egg is being added to sys.path
        commitBuilder.bisect(options.good,options.bad, testcondition=conditionscript, args_for_condition=args_for_condition)

    # Should not get here.
    else:
        print "Invalid input. Please try again."

if __name__ == "__main__":
    cli()
