"""
planex-build-mock: Wrapper around mock
"""

import os
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4

import argparse
import argcomplete
from planex.util import add_common_parser_options


def parse_args_or_exit(argv=None):
    """
    Parse command line options
    """
    parser = argparse.ArgumentParser(
        description='Planex build system in a chroot (a mock wrapper)')
    add_common_parser_options(parser)
    parser.add_argument(
        "--configdir", metavar="CONFIGDIR", default="/etc/mock",
        help="Change where the config files are found")
    parser.add_argument(
        "--root", "-r", metavar="CONFIG", default="default",
        help="Change where the config files are found")
    parser.add_argument(
        "--resultdir", metavar="RESULTDIR", default=None,
        help="Path for resulting files to be put")
    parser.add_argument(
        "--keeptmp", action="store_true",
        help="Keep temporary files")
    parser.add_argument(
        "-D", "--define", default=[], action="append",
        help="--define='MACRO EXPR' \
              define MACRO with value EXPR for the build")
    parser.add_argument(
        "--init", action="store_true",
        help="initialize the chroot, do not build anything")
    parser.add_argument(
        "--rebuild", metavar="SRPM", nargs="+", dest="srpms",
        help='rebuild the specified SRPM(s)')
    parser.add_argument(
        "--coverity", metavar="user@server",
        help="Do Coverity analysis instead of building RPMs. Commit defects" +
             " to the specified server using the specified user account.")
    parser.add_argument(
        "--cov_passwd",
        help="Password for Coverity server")
    argcomplete.autocomplete(parser)
    return parser.parse_args(argv)


def mock_shell(args, verbose = True):
    """
    Invokes "mock --shell" with provided arguments
    """
    cmd = ['mock', '--shell', args]
    if verbose:
        print("Invoking %s" % cmd)
    subprocess.check_call(cmd)


def mock_file_exists(path):
    """
    Checks if the file exists inside the mock chroot
    """
    # XXX should be a more cleaner way
    args = "ls " + path
    cmd = ['mock', '--shell', args]
    res = subprocess.call(cmd)
    if res == 0:
        return True
    else:
        return False


def mock_get_output(args):
    """
    Returns output of the performed command inside the mock chroot
    """
    cmd = ['mock', '--shell', args]
    try:
        res = subprocess.check_output(cmd)
    except subprocess.CalledProcessError:
        res = ""
    return res

def coverity(args, tmp_config_dir, srpm):
    """
    Performs coverity analysis of a given SRPM
    """

    # Coverity terminology
    #
    # Snapshot) An individual build (e.g. xen, kernel or qemu) from a specific
    #           source revision, and analysis of present defects.
    #
    # Stream)   A sequence of snapshots submitted over time, and the basic
    #           quantity of data held in the database.
    #
    # Project)  An agregate view of one or more streams, and the basic quantity
    #           of viewing/managing defects from the web interface.  It is
    #           perfectly normal for the same stream to be present in multiple
    #           different projects.

    # Coverity tools inside a mock chroot
    COV_PATH = "/opt/cov-analysis-linux64/bin"

    # Where to stash the output
    COV_OUTPUT_DIR = "/tmp/coverity"

    # XXX How to preserve history on version update?
    COV_STREAM = os.path.basename(srpm).replace(".src.rpm","")

    ### Configuration files:
    # Root configuration file
    COV_CONFIG = COV_OUTPUT_DIR + "/config/config.xml"
    # Configuration nodefs file, as referenced by default compiler templates
    COV_CONFIG_NODEF = COV_OUTPUT_DIR + "/config/user_nodefs.h"
    # cov-configure command
    COV_CONFIGURE = COV_PATH + "/cov-configure --config " + COV_CONFIG


    ### Modelling files:
    # Directory with user-supplied model.c and nodefs.h
    COV_USER_DIR = "/coverity"
    # User supplied nodef file
    COV_NODEF_SRC = COV_USER_DIR + "/nodefs.h"
    # File to create a model with
    COV_MODEL_SRC = COV_USER_DIR + "/model.c"
    # Compiled model for use with analysis
    COV_MODEL_COMPILED = COV_OUTPUT_DIR + "/" + COV_STREAM + "-model.xmldb"
    # Extra arguments when creating a model
    COV_MODEL_ARGS = "--security --concurrency"
    # cov-make-library command
    COV_MAKE_LIBRARY = COV_PATH + "/cov-make-library --config " + COV_CONFIG + \
                       " " + COV_MODEL_ARGS

    ### Analysis:
    # %{_topdir} for rpmbuild
    RPMBUILD_TOPDIR = "/builddir/build"
    # Path to strip from sources directory
    COV_STRIP_PATH = RPMBUILD_TOPDIR + "/BUILD/"
    # Checkers selection
    COV_ANALYSIS_OPTS = "--cpp --aggressiveness-level high --all --rule " + \
                        "--disable-parse-warnings --enable-fnptr"
    # cov-analyze command
    COV_ANALYZE = COV_PATH + "/cov-analyze --dir " + COV_OUTPUT_DIR + \
                  " --config " + COV_CONFIG + " -j auto --strip-path " + \
                  COV_STRIP_PATH + " " + COV_ANALYSIS_OPTS

    COV_BUILD = COV_PATH + "/cov-build --dir " + COV_OUTPUT_DIR + \
                " --fs-capture-search . --config " + COV_CONFIG

    ### Defects commiting:
    COV_HOST = args.coverity.split("@")[1]
    COV_USER = args.coverity.split("@")[0]
    COV_PASSWD = args.cov_passwd
    COV_CONNECTION_ARGS = "--host " + COV_HOST + " --user " + COV_USER + \
                           " --password " + COV_PASSWD
    COV_COMMIT_DEFECTS = COV_PATH + "/cov-commit-defects " + \
                         COV_CONNECTION_ARGS + " --dir " + COV_OUTPUT_DIR + \
                         " --config " + COV_CONFIG + " --stream " + COV_STREAM

    # XXX There is 1:1 mapping between project and stream
    COV_PROJECT = COV_STREAM
    # cov-manage-im command
    COV_MANAGE_IM = COV_PATH + "/cov-manage-im --config " + COV_CONFIG + " " + \
                    COV_CONNECTION_ARGS
    GET_COV_PROJECT = COV_MANAGE_IM + " --mode projects --show --name " + \
                      COV_STREAM + " --fields  project --nh"
    ADD_COV_PROJECT = COV_MANAGE_IM + " --mode projects --add --set name:" + \
                      COV_STREAM
    GET_COV_STREAM = COV_MANAGE_IM + " --mode streams --show --name " + \
                     COV_STREAM + " --fields  stream --nh"
    ADD_COV_STREAM = COV_MANAGE_IM + " --mode streams --add --set name:" + \
                     COV_STREAM + " --set lang:'C/C++'"
    GET_COV_PRIMARY_PROJECT = COV_MANAGE_IM + " --mode streams --show " + \
                       "--fields primary-project --name " + COV_STREAM + " --nh"
    UPDATE_COV_PROJECT = COV_MANAGE_IM + " --mode projects --update --name " + \
                         COV_STREAM + " --insert stream:" + COV_STREAM

    # Initialise a mock chroot
    cmd = ['mock', '--init']
    subprocess.check_call(cmd)

    # Install Coverity package
    cmd = ['mock', '--install', 'cov-analysis-linux64']
    subprocess.check_call(cmd)
    # XXX Install missed coverity dependency
    cmd = ['mock', '--install', 'java-1.8.0-openjdk']
    subprocess.check_call(cmd)

    # Install dependencies for SRPM
    cmd = ['mock', '--installdeps', srpm]
    subprocess.check_call(cmd)

    # Copy SRMP inside the mock chroot for manual rpmbuild
    cmd = ['mock', '--copyin', srpm, '/tmp']
    subprocess.check_call(cmd)
    srpm = os.path.basename(srpm)

    # Create a config file for Coverity
    arg = COV_CONFIGURE + " --gcc"
    mock_shell(arg)
    arg = COV_CONFIGURE + " --python"
    mock_shell(arg)
    arg = COV_CONFIGURE + " --comptype gcc --compiler cc --template"
    mock_shell(arg)

    # Create a project if needed
    if mock_get_output(GET_COV_PROJECT).split("\n")[0] != COV_PROJECT:
        arg = ADD_COV_PROJECT
        mock_shell(arg)

    # Create a stream if needed
    if mock_get_output(GET_COV_STREAM).split("\n")[0] != COV_STREAM:
        arg = ADD_COV_STREAM
        mock_shell(arg)

    # Add the stream to the project if needed
    if mock_get_output(GET_COV_PRIMARY_PROJECT).split("\n")[0] != COV_PROJECT:
        arg = UPDATE_COV_PROJECT
        mock_shell(arg)

    # Do a manual rpmbuild with "coverity wrapper"
    arg = "rpmbuild --define 'cov_wrap " + COV_BUILD + \
          "' --rebuild /tmp/" + srpm
    mock_shell(arg)

    # Install RPM with model.c and nodefs.h
    coverity_rpm = RPMBUILD_TOPDIR + "/RPMS/*coverity*.rpm"
    if mock_file_exists(coverity_rpm):
        arg = "rpm -i " + coverity_rpm
        mock_shell(arg)

    # Copy nodefs.h
    if mock_file_exists(COV_NODEF_SRC):
        arg = "cp " + COV_NODEF_SRC + " " + COV_CONFIG_NODEF
        mock_shell(arg)
    else:
        print("Couldn't find the user's nodef file at: %s" % COV_NODEF_SRC)

    if mock_file_exists(COV_MODEL_SRC):
        # Make a library from model.c
        arg = COV_MAKE_LIBRARY + " -of " + COV_MODEL_COMPILED + " " + \
              COV_MODEL_SRC
        mock_shell(arg)

        # Perform the actual Coverity analysis with a model file
        arg = COV_ANALYZE + " --user-model-file " + COV_MODEL_COMPILED
        mock_shell(arg)

    else:
        print("Couldn't find the user's model file at: %s" % COV_MODEL_SRC)
        # Perform the actual Coverity analysis
        arg = COV_ANALYZE
        mock_shell(arg)

    # Commit found defects to the server
    arg = COV_COMMIT_DEFECTS
    mock_shell(arg)


def mock(args, tmp_config_dir, *extra_params):
    """
    Return mock command line and arguments
    """
    cmd = ['mock']
    cmd += ["--uniqueext", uuid4().hex]
    cmd += ['--configdir', tmp_config_dir]

    if args.quiet:
        cmd += ['--quiet']
    if args.root is not None:
        cmd += ['--root', args.root]
    if args.resultdir is not None:
        cmd += ["--resultdir", args.resultdir]

    for define in args.define:
        cmd += ['--define', define]

    cmd.extend(extra_params)
    subprocess.check_call(cmd)


def createrepo(pkg_dir, metadata_dir, quiet=False):
    """
    Run createrepo.   Repository metadata will be created in
    metadata_dir/repodata.
    """
    cmd = ['createrepo']
    cmd += ['--baseurl=file://%s' % pkg_dir]
    cmd += ['--outputdir=%s' % metadata_dir]
    cmd += [pkg_dir]
    if quiet:
        cmd += ['--quiet']
    subprocess.check_call(cmd)


def insert_loopback_repo(config_in_path, config_out_path, repo_path):
    """
    Write a new mock config, including a loopback repository configuration
    pointing to repo_path.    Ensure that the new config file's last-modified
    time is the same as the input file's, so that the mock chroot is not
    rebuilt.
    """
    with open(config_in_path) as config_in:
        with open(config_out_path, "w") as config_out:
            for line in config_in:
                config_out.write(line)
                if "config_opts['yum.conf']" in line:
                    config_out.write("[mock-loopback-%d]\n" % os.getpid())
                    config_out.write("name=Mock output\n")
                    config_out.write("baseurl = file://%s\n" % repo_path)
                    config_out.write("gpgcheck=0\n")
                    config_out.write("priority=1\n")
                    config_out.write("enabled=1\n")
                    config_out.write("metadata_expire=0\n")
                    config_out.write("\n")
    shutil.copystat(config_in_path, config_out_path)


def clone_mock_config(configdir, tmpdir):
    """
    Copy mock configuration files into a temporary directory,
    retaining modification times to prevent the mock chroot
    cache from being rebuilt.
    Returns the path to the temporary configuration.
    """
    clonedir = os.path.join(tmpdir, "mock")
    shutil.copytree(configdir, clonedir)
    return clonedir


def main(argv=None):
    """
    Entry point
    """

    args = parse_args_or_exit(argv)

    tmpdir = tempfile.mkdtemp(prefix="px-mock-")
    config = clone_mock_config(args.configdir, tmpdir)

    try:
        if args.init:
            mock(args, config, "--init")

        else:
            config_in_path = os.path.join(args.configdir, args.root + ".cfg")
            config_out_path = os.path.join(config, args.root + ".cfg")
            insert_loopback_repo(config_in_path, config_out_path, tmpdir)
            createrepo(os.path.join(os.getcwd(), "RPMS"), tmpdir, args.quiet)

            if args.coverity:
                if len(args.coverity.split("@")) != 2:
                    print("Error: --coverity format must be user@server!")
                elif args.cov_passwd is None:
                    print("Error: --cov_passwd must be specified for " + \
                          "Coverity analysis!")
                else:
                    coverity(args, config, args.srpms[0])
            else:
                mock(args, config, "--rebuild", *args.srpms)

    except subprocess.CalledProcessError as cpe:
        sys.exit(cpe.returncode)

    finally:
        if args.keeptmp:
            print "Working directory retained at %s" % tmpdir
        else:
            shutil.rmtree(tmpdir)
