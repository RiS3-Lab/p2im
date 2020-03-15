#!/usr/bin/env python3

'''
   P2IM - script to calculate fuzzing coverage
   ------------------------------------------------------

   Copyright (C) 2018-2020 RiS3 Lab

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at:

     http://www.apache.org/licenses/LICENSE-2.0

'''

import sys, subprocess, os, re, signal, shutil
#import pickle
from pprint import pprint

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
        queue_base  = parser.get("afl", "output"),
        qemu_exe    = parser.get("qemu", "bin"),
        board       = parser.get("program", "board"),
        mcu         = parser.get("program", "mcu"),
        firmware    = parser.get("program", "img"),
        log_f       = parser.get("model", "log_file"),
        objdump     = parser.get("model", "objdump"),

        count_boot_code = True,
        queue_crashes_hangs  = ['queue', 'hangs'] if parser.getboolean("cov", "count_hang") else ['queue'],
        bbl_cov_per_case_size_to_read = parser.getint("cov", "bbl_cov_read_sz"),
        timeout     = parser.getint("cov", "timeout"),
    )

def sigterm_handler(signum, stack_frame):
    # kill all qemu instances forked
    subprocess.call(["killall", cfg.qemu_bin])
    color_print("py script is killed!", "yellow")
    sys.exit("py script is killed!")

def sigalarm_handler(signum, frame):
    global pid, killed
    killed = True
    os.kill(pid, signal.SIGTERM)
    os.kill(pid, signal.SIGKILL)

def inst_cov(cfg):
    # inst_cov = {str(hex_inst_addr): exec_no}
    inst_cov = {}
    # bbl_cov = {(str(hex_bbl_s), str(hex_bbl_e)): exec_no}
    bbl_cov = {}

    total = 0
    killed_cases = []

    if not os.path.exists(cfg.objdump):
        sys.exit("%s: not found" % cfg.objdump)
    subprocess.call("%s -Cd %s > objdump" % (cfg.objdump, cfg.firmware), shell=True)
    subprocess.call("grep '<.*>:' objdump > func_dump", shell=True)

    if not cfg.count_boot_code:
      with open('func_dump') as f:
        funcs = f.read().split('\n')
        for func in funcs:
            if cfg.non_boot_code_start in func:
                non_boot_code_start_addr = func[1:8] # format: 800xxxx
                break
      print("non_boot_code_start at func: %s, addr: %s" % (cfg.non_boot_code_start,non_boot_code_start_addr))

    #for f_i in cfg.fuzzing_inst: # used only by parallel fuzzing
    # the 2 lines below are used by non-parallel fuzzing
    if True:
        f_i = '.'

        print(f_i)

        for hangs_or_queue in cfg.queue_crashes_hangs:
            print("%s/%s" % (f_i, hangs_or_queue))

            queue_path = '%s/%s/%s' % (cfg.queue_base, f_i, hangs_or_queue)
            files = sorted(os.listdir(queue_path))

            bbl_cov_per_case_p = "bbl_cov_per_case/%s/%s" % (f_i,hangs_or_queue)
            subprocess.call(["mkdir", "-p", bbl_cov_per_case_p])

            for f in files:
                if 'id' not in f:
                    continue

                color_print("\t%s" %f)
                total += 1
                rp_f = "%s/%s" % (queue_path,f)

                f_trace = "%s/trace-%s" % (bbl_cov_per_case_p, f) # store exec trace

                # set timeout for 1s
                signal.signal(signal.SIGALRM, sigalarm_handler)
                signal.alarm(cfg.timeout)

                with open('%s/%s' % (bbl_cov_per_case_p, f),'w') as f_tmp:
                    cmd = [cfg.qemu_exe, "-nographic", "-aflFile", rp_f, 
                      "-board", cfg.board, "-mcu", cfg.mcu, "-image", cfg.firmware, 
                      "-pm-stage", "3", "-model-input", args.model_if,
                      # only dump trace on stage 3 for coverage calculation purpose
                      # XXX fclose(trace_f) may not be invoked. According to C 
                      # standard, fclose is invoked at exit()
                      "-trace", f_trace]
                    #print "cmd: %s" % ' '.join(cmd)
                    proc = subprocess.Popen(cmd, stdout=f_tmp, stderr=subprocess.PIPE)
                    #proc = subprocess.Popen(cmd, stdout=f_tmp)
                global pid, killed
                pid = proc.pid
                killed = False
                proc.wait()
                if not killed:
                    # clear timeout value
                    signal.alarm(0)
                else:
                    print("PID: %s, Case: %s times out and killed" % (pid, f))
                    killed_cases.append("%s/%s" % (hangs_or_queue, f))

                    # qemu killed in sig handler
                    #subprocess.call("ps -ef | grep %s | wc -l" % cfg.qemu_exe, shell=True)
                    #subprocess.call(["killall", cfg.qemu_exe])


                with open(f_trace) as f_tmp:
                #with open('%s/%s' % (bbl_cov_per_case_p, f)) as f_tmp:
                    op = f_tmp.read(cfg.bbl_cov_per_case_size_to_read)
                    if not cfg.count_boot_code:
                        #print "non_boot_code_start_index/len = %d/%d" % (op.find(non_boot_code_start_addr),len(op))
                        op = op[op.find(non_boot_code_start_addr):]
                os.remove(f_trace)

                # use group primitive--() to retreive start, end of each BBL
                BBLs = re.findall('BBL \((0x[0-9a-f]+), (0x[0-9a-f]+)\)', op)
                for (start, end) in BBLs:
                    s = int(start, 16)
                    e = int(end, 16)

                    # calculate bbl coverage
                    bbl = (hex(s), hex(e))
                    if bbl not in bbl_cov:
                      bbl_cov[bbl] = 1
                    else:
                      bbl_cov[bbl] += 1

                    # calculate instruction coverage
                    i = s
                    while (i < e):
                        i_hex = hex(i) # a hex_str representing i
                        if i_hex not in inst_cov:
                            inst_cov[i_hex] = 1
                        else:
                            inst_cov[i_hex] += 1
                        i += 2

    with open("bbl_cnt", "w") as of:
        pprint(len(bbl_cov), stream=of)
    with open("bbl_cov", "w") as of:
        pprint(bbl_cov, stream=of)

    with open("inst_cov_w_boot" if cfg.count_boot_code else "inst_cov", "w") as of:
        #pickle.dump(inst_cov, of)
        pprint(inst_cov, stream=of)
    with open("inst_executed_w_boot" if cfg.count_boot_code else "inst_executed", "w") as of:
        #pickle.dump(sorted(inst_cov.keys()), of)
        pprint(sorted(inst_cov.keys()), stream=of)

    print("cases killed by timeout: ")
    print(killed_cases)

    print("total valid cases processed %d" % total)


def func_cov(cfg):
    '''
    Change checklist before measuring coverage
    None, as objdump & func_dump is already generated by inst_cov
    '''

    ## read in input from files
    # {int(addr): str(func_name)}, sorted by addr
    ft = {}
    # generated by:
    # arm-none-eabi-objdump -Cd BUILD/NUCLEO_F103RB/GCC_ARM/mbed.elf > objdump3
    # grep '<.*>:' objdump3 > func_dump
    # f is in format of 'addr name\n'
    print('Reading func_dump...')
    with open('func_dump') as f:
        for l in f:
            l = l.replace(':\n','')
            if '\t' in l:
                # exclude ' 80006ca:    f000 fd94     bl    80011f6 <mbed::Callback<void ()>::Callback(void (*)())>'
                continue
            (addr, name) = l.split(' ',1)
            ft[int(addr, 16)] = name[1:-1]
    # XXX: actually unnecessary as last few labels are actual data
    #text_end_addr = 0x8016fd4
    #ft[text_end_addr] = ".text_end"

    # {int(addr): int(hit_no)}, sorted by addr
    ic = {}
    # generated by inst_cov.py
    # f is printed by pprint from a dict
    print('Reading %s ...' % "inst_cov_w_boot" if cfg.count_boot_code else "inst_cov")
    with open("inst_cov_w_boot" if cfg.count_boot_code else "inst_cov") as f:
        for l in f:
            l = l.replace('{','').replace('}','').replace(',','') \
                .replace('\'','').replace('\n','')
            (addr, cnt) = l.split(': ')
            ic[int(addr, 16)] = int(cnt)
    #print (ft)
    #print (ic)


    ## output
    # merge of func_dump and inst_cov by the order of addr
    # {}
    merge = {}
    # {func_name: True|False}
    func_cov = {}

    ## linear scan and merge
    i = 0
    j = 0
    ft_ks = sorted(ft.keys())
    ic_ks = sorted(ic.keys())

    print('linear scan and merge...')
    while i < len(ft)-1:
        ft_k = hex(ft_ks[i]) # hex str
        ft_v = ft[ft_ks[i]] # cannot directly use ft_k, a hex str

        merge[ft_k] = (ft_v, None)
        func_cov[ft_k] = (ft_v, False)

        while j < len(ic) and ic_ks[j] < ft_ks[i+1]:
            ic_k = hex(ic_ks[j]) # hex str
            ic_v = ic[ic_ks[j]] # cannot directly use ic_k, a hex str

            if ic_k not in list(merge.keys()):
                merge[ic_k] = (None, ic_v) # or use (func_name, ic_v)
            else:
                merge[ic_k] = (merge[ic_k][0], ic_v)
            func_cov[ft_k] = (ft_v, True)
            j += 1
        i += 1

    print('ouput to func_cov and func_cov_merge...')
    with open("func_cov_merge_w_boot" if cfg.count_boot_code else "func_cov_merge", "w") as f:
        #pickle.dump(merge, f)
        pprint(merge, stream=f)
    with open("func_cov_w_boot" if cfg.count_boot_code else "func_cov", "w") as f:
        # statistics
        # cal fun cov (percent)
        t = [ts[1] for ts in list(func_cov.values()) if ts[1] is True]
        f.write("Function coverage: %d/%d = %.2f%%\n\n" % (len(t), len(func_cov),
            len(t)*100.0/len(func_cov)))

        #pickle.dump(func_cov,f)
        pprint(func_cov,stream=f)

    print('Done')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Count instruction and function coverage")
    parser.add_argument("--model-if", dest="model_if", required=True,
        help="peripheral model file (required).", type=os.path.abspath)
    parser.add_argument("-c", "--config", dest="config", required=True, 
        help="configuration file (required).", type=os.path.abspath)

    args = parser.parse_args()
    #print(args)

    cfg = read_config(args.config)

    cov_path = "%s/coverage" % cfg.working_dir
    if os.path.isdir(cov_path):
        shutil.rmtree(cov_path)
    os.makedirs(cov_path)

    color_print("Change working dir to: %s" % cov_path, "blue")
    os.chdir(cov_path)
    print("CWD: %s\n" % os.getcwd())

    signal.signal(signal.SIGTERM, sigterm_handler)

    try:
        if not os.path.isfile(cfg.firmware):
            print("Cannot find firmware bin in current dir!")
            sys.exit(-1)

        inst_cov(cfg)
        print("inst_cov finished!\n\n")

        func_cov(cfg)
        print("func_cov finished!\n\n")

    except KeyboardInterrupt:
        print("\nKeyboard Interrupted!")
    finally:
        # Remove the space consuming intermediate result
        if os.path.isdir("bbl_cov_per_case"):
            shutil.rmtree("bbl_cov_per_case")
    '''
    finally:
        # clean-up
        print "(Number+1) of cases timeout:"
        subprocess.call("ps -ef | grep %s | wc -l" % cfg.qemu_exe, shell=True)
        subprocess.call(["killall", cfg.qemu_exe])

        print "(Number+1) of cases cannot be killed by SIGTERM:"
        subprocess.call("ps -ef | grep %s | wc -l" % qemu_exe, shell=True)
        print "Do SIGKILL"
        subprocess.call(["killall", "-9", qemu_exe])
    '''
