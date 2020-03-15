#!/usr/bin/env python3

'''
   P2IM - script to orchestrate fuzzing campaign
   ------------------------------------------------------

   Copyright (C) 2018-2020 RiS3 Lab

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at:

     http://www.apache.org/licenses/LICENSE-2.0

'''

import subprocess,sys,os,json,logging,shutil,signal,time,stat

import configparser
import argparse
from argparse import Namespace

def color_print(s, color="green"):
    if color == "green":
        print("\033[92m%s\033[0m" % s)
    elif color == "blue":
        print("\033[94m%s\033[0m" % s)
    elif color == "yellow":
        print("\033[93m%s\033[0m" % s)
    elif color == "red":
        print("\033[91m%s\033[0m" % s)
    else:
        print(s)

def read_config(cfg_f):
    if not os.path.isfile(cfg_f):
        sys.exit("Cannot find the specified configuration file: %s" % cfg_f)
    parser = configparser.SafeConfigParser()
    parser.read(cfg_f)

    return Namespace(
        working_dir = parser.get("DEFAULT", "working_dir"),
        prog        = parser.get("DEFAULT", "program"),
        run         = parser.get("DEFAULT", "run"),
        afl_bin     = parser.get("afl", "bin"),
        afl_timeout = parser.get("afl", "timeout"),
        afl_seed    = parser.get("afl", "input"),
        afl_output  = parser.get("afl", "output"),
        qemu_bin    = parser.get("qemu", "bin"),
        board       = parser.get("program", "board"),
        mcu         = parser.get("program", "mcu"),
        img         = parser.get("program", "img"),
        log_f       = parser.get("model", "log_file"),
        me_bin      = parser.get("model", "bin"),
    )

def sigalarm_handler(signum, frame):
    global pid, killed
    killed = True
    os.kill(pid, signal.SIGTERM)
    os.kill(pid, signal.SIGKILL)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Helper script to launch fuzzer")
    parser.add_argument("--model-if", dest="model_if", default=None,
        help="optional peripheral model file")
    parser.add_argument("-c", "--config", dest="config", 
        default="%s/fuzz.cfg" % os.path.dirname(os.path.abspath(sys.argv[0])),
        help="configuration file. Default: fuzz.cfg")
    parser.add_argument("--no-fuzzing", dest="no_fuzzing",
        action='store_true', help="don't run fuzzer")
    parser.add_argument("--no-skip-deterministic", dest="no_skip_deterministic",
        action='store_true', help="don't skip deterministic steps of afl")
    # TODO option for resume fuzzer

    args = parser.parse_args()
    #print args

    if args.config:
      args.config = os.path.abspath(args.config)

    if args.model_if and not os.path.isfile(args.model_if):
      sys.exit("model file provided with --model-if doesn't exist.")

    cfg = read_config(args.config)

    color_print("Change working dir to: %s" % cfg.working_dir, "blue")
    os.chdir(cfg.working_dir)
    print("CWD: %s\n" % os.getcwd())
    #shutil.copyfile(args.config, "fuzz.cfg")

    logging.basicConfig(filename=cfg.log_f, level=logging.INFO,
        format='[%(asctime)s] %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')
    logging.info("fresh fuzzer run")


    # try our best to extract model w/o input
    color_print("try our best to extract model w/o input", "blue")
    run_num = "0"

    if os.path.isdir(run_num):
      color_print("model already extracted! No need to do it again")
    else:
      logging.info("run_num %s, access_to_unmodeled_peri: %s" % (run_num, {}))
      start_time = time.time()

      cmd_me0 = [cfg.me_bin, "-c", args.config, "--run-num", run_num,
        "--print-to-file"]
      if args.model_if:
        cmd_me0 += ["-model-input", args.model_if]
      print("cmd_me0: %s\n" % ' '.join(cmd_me0))
      subprocess.call(cmd_me0)

      logging.info("execution time: %f" % (time.time() - start_time))

    args.model_if = os.path.abspath("%s/peripheral_model.json" % run_num)
    print('')


    # extract model for each seed input
    color_print("extract model for each seed input", "blue")
    prev_run_num = run_num

    for seed0 in os.listdir(cfg.afl_seed):
      seed = "%s/%s" % (cfg.afl_seed, seed0)
      seed_run = 1

      while True: # may extract multiple rounds
        run_num = "%s.%s.%d" % (prev_run_num, seed0, seed_run)
        color_print(run_num)


        color_print("run f/w w/ seed input to check if there is aup")
        cmd_qemu = [cfg.qemu_bin, "-nographic", "-aflFile", seed,
          "-board", cfg.board, "-mcu", cfg.mcu, "-image", cfg.img,
          "-pm-stage", "3", "-model-input", args.model_if, 
          # options below are not used in no forkserver mode
          "-me-bin", cfg.me_bin, "-me-config", args.config]
        print("cmd_qemu: %s\n" % ' '.join(cmd_qemu))

        # set timeout for 1s
        signal.signal(signal.SIGALRM, sigalarm_handler)
        signal.alarm(1)

        with open(os.devnull, 'w') as devnull:
          proc = subprocess.Popen(cmd_qemu, stdout=devnull, stderr=devnull)

        global pid, killed
        pid = proc.pid
        killed = False
        proc.wait()
        if not killed:
          # clear timeout value
          signal.alarm(0)
        else:
          color_print("qemu hangs(pid: %d). seed input should not hang!" % pid, "red")
          # qemu killed in sig handler

        # check if there is aup
        if proc.returncode not in [0x40, 0x41]:
          color_print("No aup, don't run ME")
          break


        color_print("There is aup, run ME")
        cmd_me = [cfg.me_bin, "-c", args.config, "--run-num", run_num,
          "--print-to-file", "--run-from-forkserver", "--afl-file", seed, 
          "--model-if", args.model_if]
        print("cmd_me: %s" % ' '.join(cmd_me))
        subprocess.call(cmd_me)

        args.model_if = os.path.abspath("%s/peripheral_model.json" % run_num)
        seed_run += 1
        print('')
    print('')


    # launch fuzzer
    color_print("launch fuzzer", "blue")

    cmd_afl = [cfg.afl_bin, "-i", cfg.afl_seed, "-o", cfg.afl_output, 
        "-t", cfg.afl_timeout, "-QQ", 
        # used by only non forkserver mode
        # AFL passes model_if to qemu, so we don't pass it here
        "-a", cfg.me_bin, "-b", args.config, "-c", args.model_if, 
        "-T", "%s_%s" % (cfg.prog, cfg.run)]
    if not args.no_skip_deterministic:
        # skip deterministic stage
        cmd_afl += ["-d"]
    # end of afl options 

    cmd_afl_qemu = [cfg.qemu_bin, "-nographic",
        "-board", cfg.board, "-mcu", cfg.mcu, "-image", cfg.img,
        "-pm-stage", "3", "-aflFile", "@@", 
        # options below are not used in no forkserver mode
        #"-me-bin", cfg.me_bin, "-me-config", args.config, 
        #"-model-input", args.model_if
    ]
    cmd_afl += cmd_afl_qemu

    # run_fw.sh for crash triage
    with open("run_fw.py", "w") as f:
        f.write("#!/usr/bin/env python3\n")
        f.write("import sys,subprocess\n")
        f.write("if len(sys.argv) < 3 or len(sys.argv) > 4:\n")
        f.write("    print(\"Usage: %s last_round_of_model_instantiation test_case [--debug]\" % sys.argv[0])\n")
        f.write("    print(\"\t--debug argument is optional. It halts QEMU and wait for a debugger to be attached\")\n")
        f.write("    sys.exit(-1)\n")
        f.write("\n")
        # replace "'@@'" with "sys.argv[2]"
        cmd = str(cmd_afl_qemu + ["-model-input", "%s/%%s/peripheral_model.json %% sys.argv[1]" % cfg.working_dir]).replace("'@@'", "sys.argv[2]").replace(" % sys.argv[1]'", "' % sys.argv[1]")
        f.write("cmd = %s\n" % str(cmd))
        f.write("\n")
        f.write("if len(sys.argv) == 4 and sys.argv[3] == '--debug':\n")
        f.write("    # halt qemu and wait for a debugger to be attached\n")
        f.write("    cmd+=%s\n" % str(["-gdb", "tcp::9000", "-S"]))
        f.write("print(cmd)\n")
        f.write("\n")
        f.write("subprocess.call(cmd)\n")
    os.chmod("run_fw.py", stat.S_IRWXU)

    print("cmd_afl: %s\n" % ' '.join(cmd_afl))

    if not args.no_fuzzing:
      subprocess.call(cmd_afl, env=dict(os.environ, AFL_NO_FORKSRV=''))
