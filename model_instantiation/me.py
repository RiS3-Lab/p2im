#!/usr/bin/env python3

'''
   P2IM - script to orchestrate model instantiation
   ------------------------------------------------------

   Copyright (C) 2018-2020 RiS3 Lab

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at:

     http://www.apache.org/licenses/LICENSE-2.0

'''

import subprocess,json,sys,os,re,hashlib,signal,atexit,shutil,logging,time,csv
import struct

import configparser
import argparse
from argparse import Namespace


def cmp(a, b):
    r = 0 if a.__eq__(b) else 1
    return r

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
        qemu_bin    = os.path.abspath(parser.get("qemu", "bin")),
        qemu_log    = parser.get("qemu", "log"),
        board       = parser.get("program", "board"),
        mcu         = parser.get("program", "mcu"),
        img         = os.path.abspath(parser.get("program", "img")),
        log_f       = os.path.abspath(parser.get("model", "log_file")),
        retry_num   = parser.getint("model", "retry_num"),
        peri_addr_range = parser.getint("model", "peri_addr_range"),
        objdump     = parser.get("model", "objdump"),
    )

def one_sr_input_gen(sr_bits, one_cnt, one_name, prev_set_bit, set_bits):
    # generate input for one sr
    # @set_bits: number of bits set in each input
    # @prev_set_bit: bit_i+1 < bit_i to avoid duplicate
    one_sr = []
    if set_bits >= 1:
      for i in range(0, prev_set_bit):
        one_sr += one_sr_input_gen(sr_bits, one_cnt+(1<<i), 
          one_name+("%02d+" % i).encode(), i, set_bits-1)
    else:
      # struct.pack: always packs one_cnt 4-byte unsigned int, regardless of sr_bits
      one_sr.append((struct.pack(">I",one_cnt), ("bit:%s," % one_name.decode()[:-1]).encode()))
    return one_sr # [('\xXX\xXX\xXX\xXX', "bit:01+02,")]

def input_f_writer(sr_bits, sr_num, sr_dir, content, fname, set_bits):
    arr = []
    if sr_num >= 1:
        # gen input for 1 SR setting @set_bits bits, and add a baseline
        one_sr = one_sr_input_gen(sr_bits, 0, b"", sr_bits, set_bits)
        one_sr.append((struct.pack(">I",0), ("bit:%s," % ("-1+"*set_bits)[:-1]).encode()))

        for (one_cnt, one_name) in one_sr:
            arr += input_f_writer(sr_bits, sr_num-1, sr_dir,
                content+one_cnt, fname+one_name, set_bits)
    else:
        fname = fname[:-1] # trim last ','
        with open("%s/%s" % (sr_dir, fname.decode()), "wb") as f:
            f.write(content)
        arr.append(fname)
    return arr # flat array of filenames

def exec_trace_sig(sr_bits, sr_num, trace_dir, fname, set_bits):
    dic = {}
    if sr_num >= 1:
        # gen input for 1 SR setting @set_bits bits, and add a baseline
        one_sr = one_sr_input_gen(sr_bits, 0, b"", sr_bits, set_bits)
        one_sr.append((struct.pack(">I", 0), ("bit:%s," % ("-1+" * set_bits)[:-1]).encode()))

        for (one_cnt, one_name) in one_sr:
            # one_name = "bit:01+02," -> bits_int = "01+02"
            bits_int = one_name[4:-1]
            dic[bits_int.decode()] = exec_trace_sig(sr_bits, sr_num-1, trace_dir,
                fname+one_name, set_bits)

        s = ""
        d = {}
        for k, v in list(dic.items()):
            # k = "01+02" -> [1, 2]
            k = list(map(int, k.split('+')))
            if v["sig"] not in d:
                d[v["sig"]] = [k]
            else:
                d[v["sig"]].append(k)

        dic["summary"] = d
        dic["sig"] = hashlib.md5(str(dic).encode()).hexdigest()

    else:
        fname = fname[:-1] # trim last ','
        with open("%s/%s" % (trace_dir, fname.decode()), "r") as f:
            dic["sig"] = hashlib.md5(f.read().encode()).hexdigest()
    # dic = {"AB+CD":{nested dic}, "sig":sig, "summary":{sig:[[bits],]}}
    return dic

def driver_checked_bits(sr_num, trace_sig, set_bits):
    arr = []
    if sr_num >= 1:
        # cnt = [[count, [[SR1_bitx, SR1_bity]]],], count is only used in print
        # without count, we can merge cnt[] and bits[]
        cnt = []
        max_cnt = 0
        max_idx = -1

        # each checked bit will incur a distinct trace
        # unchecked bits share the same, most common trace.
        for v in list(trace_sig["summary"].values()):
            if len(v) > max_cnt:
                max_cnt = len(v)
                max_idx = len(cnt)
            cnt.append([len(v), v])

        # delete the most common trace, and cnt only contains chekced bits
        del cnt[max_idx]

        # only contains checked bits
        bits = []
        for c in cnt:
            bits += c[1]
        bits.sort()
        print("%d-th from the last sr: checked bits: %s" % (sr_num, bits))
        trace_sig["checked_bits"] = bits

        if len(bits) == 0:
            color_print("\tNo bits are checked! May not be a SR!", "red")

        # instead of randomly pick an unchecked bits, we use -1
        # we assume -1 is not in bits[]. TODO justify it
        baseline = [-1]*set_bits
        if baseline in bits:
            color_print("\tUnexpected: -1 is in checked bits!", "red")

        for bit in [baseline] + bits:
            # bit = [AB,CD] -> bit_s = 'AB+CD'
            bit_s = '+'.join([str(x).zfill(2) for x in bit])
            arr2 = driver_checked_bits(sr_num-1, trace_sig[bit_s], set_bits)
            for a in arr2:
                a.insert(0, bit)
            # arr = [[[SR1_bitx, SR1_bity], ..., [SRn_bitx, SRn_bity]],]
            arr += arr2
    else:
        arr.append([])
    return arr

def qemu_run(cmd, retry_num, stage):
    rv = {1: [0x20, 0x19, 0x30], 2: [0x21, 0x23],
          1.1: [0x20, 0x30]}
    error_rv = {2: {0x24: "Cannot find SR Model which is supposed to exist"}}

    run_num = 0
    while run_num < retry_num:
        with open(os.devnull, 'w') as devnull:
            # TODO may need timeout
            ret_val = subprocess.call(cmd, stdout=devnull, stderr=devnull)
        print("ret_val: 0x%x" % ret_val)
        if ret_val in rv[stage]:
            return ret_val
        color_print("ret_val == 0x%x, re-run it!" % ret_val, "red")
        run_num += 1

    color_print(error_rv[stage][ret_val], "red")
    sys.exit("Stage %d returned due to unexpected reasons!" % stage)

def cnt_bbl_cov(trace_f):
    bc = {}
    # bc = {(hex_str(bbl_s), hex_str(bbl_e)): cnt}
    bbls = re.findall('BBL \((0x[0-9a-f]+), (0x[0-9a-f]+)\)', open(trace_f).read())
    for bbl in bbls:
        if bbl in bc:
            bc[bbl] += 1
        else:
            bc[bbl] = 1
    return bc

def sig_handler(signo, stack_frame):
    # kill all qemu instances forked
    subprocess.call(["killall", cfg.qemu_bin])
    color_print("py script is killed!", "yellow")
    sys.exit("py script is killed!")

def model_stat(model):
    peri_l = list(model["model"].keys())
    evt_peri_l = [k for k,v in list(model["model"].items()) if len(v["events"])]

    stat = {"peri_num": len(peri_l), "peri_list": sorted(peri_l), 
            "peri_with_event_num": len(evt_peri_l), 
            "peri_with_event_list": sorted(evt_peri_l)}
    return stat

def exit_callback():
    color_print("\nexit_callback is invoked")
    json.dump(rc_adjusted_sum, open("rc_adjusted_sum" ,"w"), sort_keys=True, indent=4)

    bbl_cov_j = {}
    for k,v in list(bbl_cov.items()):
      bbl_cov_j[str(k)] = v
    json.dump({"bbl_cov": bbl_cov_j, "bbl_num": len(bbl_cov_j)}, open("bbl_cov", "w"), indent=4)

    # copy model to peripheral_model.json
    model_of_final = "peripheral_model.json"
    if last_peri_model:
        # sanitize the extracted model and copy to model_of_final
        model = json.load(open(last_peri_model))
        model.pop("sr_read", None)

        m = model["model"]
        for peri in list(model["model"].values()):
          for reg in peri["regs"]:
            reg.pop("cr_value", None)

        if args.run_from_fs:
          del model["access_to_unmodeled_peri"]

        # calculate statistics
        stat = model_stat(model)
        model["statistics"] = stat

        color_print("Last model extracted:")
        print(last_peri_model)
    else:
        # dump an empty model
        model = {"model":{}}

    # calculate metrics
    if args.gt:
        json.dump(model, open(model_of_final, "w"), sort_keys=True, indent=4)
        import statFp3
        model["metrics"] = statFp3.getStat(model_of_final, args.gt, "metric.csv")

    json.dump(model, open(model_of_final, "w"), sort_keys=True, indent=4)
    print('')

    # calculate time of execution
    exec_time = time.time() - start_time
    color_print("Execution time(seconds): ")
    print(exec_time)
    if args.run_from_fs:
        logging.info("execution time: %f" % exec_time)

def adjusted_reg_cat(rc_diff):
    rca = {"cr_del": [], "cr_ins": [], "sr_del": [], "sr_ins": []}
    for k,v in list(rc_diff.items()):
      ot = v["old"]["type"]
      nt = v["new"][0]["type"]
      # for CR_SR, CR->CR_SR is the only adjustment allowed currently
      # so we only handle this case
      if ot != nt:
        if ot == 1 and nt != 4: # CR->CR_SR is not cr_del
          rca["cr_del"].append(k)
        if ot == 2:
          rca["sr_del"].append(k)
        if nt == 1 or nt == 4 and ot != 1:
          # CR_ins include both adjusted to and newly cat'd CR
          rca["cr_ins"].append(k)
        #if nt == 2:
          # TODO SR_ins include both adjusted to and newly cat'd SR
        if nt == 2 and ot != 0:
          rca["sr_ins"].append(k)

    for v in list(rca.values()):
      if v:
        return (rca, True) # 2nd param: adjusted
    return (rca, False)

def handle_sr_cr_del(prereq, rca):
    prereq_upd = {}
    for peri_cfg, v in list(prereq.items()):
      # peri_cfg = "CR_val", v = {srr_site:{}}

      # delete all reg in cr_del from CR_val
      if peri_cfg:
        peri_cfg_upd = []
        for cr in peri_cfg.split(','):
          bit, value = cr.split(':')
          if bit not in rca["cr_del"]:
            peri_cfg_upd.append((int(bit), value))

        CR_val = ""
        for cr_upd in peri_cfg_upd:
          CR_val += "%d:%s," % (cr_upd[0], cr_upd[1])
        CR_val = CR_val[:-1] # trim the ',' at the end
      else:
        # peri_cfg == ""
        CR_val = peri_cfg


      # delete all srr_site extracted due to any reg in SR_del
      v_upd = {}
      for srr_site, v1 in list(v.items()):
        if v1["sr_num"] == 1:
          # TODO multi-SR
          if v1["sr_idx"][0] not in rca["sr_del"]:
            v_upd[srr_site] = v1

      prereq_upd[CR_val] = v_upd

    return prereq_upd


def stage1(redo=False):
    global model_if, model_of, bbl_cov, model_if_s1
    # global args, cfg, depth, stage_str, cmd_base

    stage = 1 if not redo else 1.1
    model_if = model_of if depth > 1 or redo else args.model_if
    model_of = "model-depth:%s,stage:%.1f.json" % (depth,stage)
    trace_f = "trace-depth:%s,stage:%.1f" % (depth,stage)
    reg_acc_f = "reg_acc-depth:%s,stage:%.1f" % (depth,stage)
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    cmd = cmd_base + ["-pm-stage", str(int(stage)), 
      "-trace", trace_f, "-reg-acc", reg_acc_f,
      "-model-output", model_of]
    if model_if:
      cmd += ["-model-input", model_if]
    if args.run_from_fs:
      cmd += ["-aflFile", args.afl_file]
    print("cmd: %s" % ' '.join(cmd))

    ret_val = qemu_run(cmd, cfg.retry_num, stage)

    bbl_cov = cnt_bbl_cov(trace_f)
    #print bbl_cov

    model_if_s1 = model_if

    print('')
    return ret_val
 

def stage1_4():
    global model_if, model_of, rc_adjusted_sum
    # global depth, stage_str, model_if_s1

    stage = 1.4
    model_if = model_of
    model_of = "model-depth:%s,stage:%.1f.json" % (depth,stage)
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    mn = json.load(open(model_if)) # model_of of stage 1

    cr_ins_happen = False
    if model_if_s1: # model_if of stage 1
      # always True except depth 1 under an empty --model-if
      mo = json.load(open(model_if_s1))

      for peri_ba0 in mo["model"]:
        # not in 1st depth of a peripheral,
        # where every reg is newly categorized and cannot be adjusted
        color_print("peri_ba: %s" % peri_ba0)

        # rc_diff = {reg_idx: {old: {type:, r/w/:, attr:}, new: [{type:, r/w:, attr:}]}}
        # maintain the same structure with stage 2
        rc_diff = {}
        old = mo["model"][peri_ba0]["regs"]
        new = mn["model"][peri_ba0]["regs"] # new is dumped
        # extend old to the same length with new
        for i in range(len(old), len(new)):
          old.append({"type": 0})
        for i in range(0, len(new)):
          # don't consider cr_value when cmp two reg cat
          old[i].pop("cr_value", None)
          new_i = dict(new[i]) # copy new[i] to avoid del cr_val in orig copy
          new_i.pop("cr_value", None)
          if cmp(old[i], new_i) != 0: # not equal
            rc_diff[i] = {"old": old[i], "new": [new_i]}

        color_print("Register category updates since last srr: ")
        json.dump(rc_diff, sys.stdout, indent=4)
        #print rc_diff

        # detect rc adjusted
        # rc_adjusted = {"cr_del":[], "cr_ins":[], "sr_del":[], "sr_ins":[]}
        (rc_adjusted, adjusted) = adjusted_reg_cat(rc_diff)
        if adjusted:
          k = "depth:%s,stage:%.1f" % (depth,stage)
          if k not in rc_adjusted_sum:
            rc_adjusted_sum[k] = {peri_ba0: rc_adjusted}
          else:
            rc_adjusted_sum[k][peri_ba0] = rc_adjusted
          color_print("Register category adjusted: ")
          print(rc_adjusted)


          if rc_adjusted["cr_ins"]:
            color_print("cr_ins observed, restart ME with reg cat "
              "extracted!\n", "yellow")

            cr_ins_happen = True
            mn["model"][peri_ba0]["events"] = {}

            # since restart with empty prereq for peri w/ cr_ins, 
            # no need to do the following adjustment
            continue

          if rc_adjusted["cr_del"] or rc_adjusted["sr_del"]:
            color_print("cr/sr_del observed, adjust prereq!", "yellow")
            mn["model"][peri_ba0]["events"] = \
              handle_sr_cr_del(mn["model"][peri_ba0]["events"], rc_adjusted)
            # TODO check whether the SR_RS is already modeled after cr_del
            # otherwise it will cause duplicate tuples in satisfy/ns
            # alternative solution is to modify the end of stage 2.5

          if rc_adjusted["sr_ins"]:
            # TODO
            color_print("sr_ins observed, redo stage 1 of current depth!\n", 
              "yellow")

        else:
          color_print("No register category adjustment")
        print('')
      # end of for peri_ba0 in mo["model"]

    json.dump(mn, open(model_of, "w"), indent=4)

    print('')
    return cr_ins_happen


def stage1_5():
    global model_if, model_of
    # global depth, stage_str

    stage = 1.5
    model_if = model_of
    model_of = "model-depth:%s,stage:%.1f.json" % (depth,stage)
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    model = json.load(open(model_if))

    sr_r = model["sr_read"]

    sr_num = sr_r["sr_num"]
    if sr_num not in list(range(1,5)): # assume at most 4 SR
        sys.exit("Unexpected SR cnt: %d" % sr_num)

    # correlate sr to peripherals
    peri_ba = hex(sr_r["peri_base_addr"]) # int -> hex_str = '0x...'
    sr_idx = sr_r["sr_idx"]

    # if > 0 (checked at stage2_5), CR_SR (at most 1) requires SMR
    CR_SR_r_idx = sr_r["CR_SR_r_idx"]

    # CR_val
    regs = model["model"][peri_ba]["regs"]
    CR_val = ""
    for i in range(0, len(regs)):
      if regs[i]["type"] == 1 or regs[i]["type"] == 4:
        CR_val += "%d:%s," % (i, regs[i]["cr_value"])
    CR_val = CR_val[:-1] # trim the ',' at the end

    # use bbl_e to denote SR readsite
    srr_bbl_e = hex(sr_r["bbl_e"]) # int -> hex_str = '0x...'

    # number of bits in SR
    sr_bits = model["model"][peri_ba]["reg_size"] * 8

    # evaluation only
    srr_bbl_cnt = sr_r["bbl_cnt"]
    srr_func = sr_r["sr_func"]

    # determine when to terminate worker of stage 2
    objdump = subprocess.check_output([cfg.objdump, "-dC", cfg.img])
    callsites = re.findall("<%s>\n *([0-9a-f]+?):" % sr_r["sr_func"], objdump.decode("utf-8") )
    sr_r["sr_func_ret_addr"] = [int(i, 16) for i in callsites]

    json.dump(model, open(model_of, "w"), indent=4)

    color_print("bbl_cnt, peri_ba, sr_idx, peri_cfg, bbl_id, srr_func:")
    print('%d, %s, %s, %s, %s, %s' % \
      (srr_bbl_cnt, peri_ba, sr_idx, CR_val, srr_bbl_e, srr_func))

    print('')
    return Namespace(
        sr_num       = sr_num,
        peri_ba      = peri_ba,
        sr_idx       = sr_idx,
        CR_SR_r_idx  = CR_SR_r_idx,
        CR_val       = CR_val,
        srr_bbl_e    = srr_bbl_e,
        sr_bits      = sr_bits,
        # evaluation only
        srr_bbl_cnt  = srr_bbl_cnt,
        srr_func     = srr_func,
    )


def stage1_9(srr_info, inv_num=1):
    global model_if, model_of
    # global depth, stage_str

    stage = 1.9
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    # prepare input files
    sr_dir = "sr_input-depth:%d" % depth
    if inv_num > 1:
        color_print("\tinvocation number: %d" % inv_num, "blue")
        sr_dir += ",invoc:%d" % inv_num
    if not os.path.exists(sr_dir):
        os.makedirs(sr_dir)

    set_bits = 2 if inv_num == 3 else 1
    fname_l = input_f_writer(srr_info.sr_bits, srr_info.sr_num, sr_dir, b"", b"",
        set_bits)

    print('')
    return sr_dir, fname_l


def stage2(srr_info, sr_dir, fname_l, inv_num=1):
    global model_if
    # global args, cfg, depth, stage_str, model_of

    stage = 2
    model_if = model_of
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    s2_dir = "depth:%d,stage:%.1f" % (depth,stage)
    if inv_num > 1:
        color_print("\tinvocation number: %d" % inv_num, "blue")
        s2_dir += ",invoc:%d" % inv_num
    if not os.path.exists(s2_dir):
        os.makedirs(s2_dir)

    if inv_num == 2: # XXX tentative impl
        model = json.load(open(model_if))
        sr_r = model["sr_read"]

        objdump = subprocess.check_output([cfg.objdump, "-dC", cfg.img])
        objdump = objdump.decode()
        func_l = re.findall("([0-9a-f]+) <(.*?)>:", objdump) # [(addr, name)]

        callsites1 = re.findall("<%s>\n *([0-9a-f]+?):" % sr_r["sr_func"], objdump)
        callsites2 = []
        for cs1 in callsites1:
            # figure out funct of cs1
            for i in range(0, len(func_l)):
                if int(func_l[i][0], 16) > int(cs1, 16):
                    break
            func_name = func_l[i-1][1]
            callsites2 += re.findall("<%s>\n *([0-9a-f]+?):" % func_name, objdump)

        sr_r["sr_func_ret_addr"] = [int(i, 16) for i in callsites2]
        color_print("func_ret == 2, Return addr: ", "yellow")
        print(callsites2)
        if not callsites2:
          color_print("empty return addr for func_ret == 2!", "red")

        json.dump(model, open(model_of, "w"), indent=4)

    # run program and collect feedback(written to file)
    # term_cond0 = {fname: ret_val}
    term_cond0 = {}
    for fname_b in fname_l:
        fname=fname_b.decode()
        sr_input = "%s/%s" % (sr_dir,fname)
        trace_f = "%s/trace-%s" % (s2_dir,fname)
        reg_acc_f = "%s/reg_acc-%s" % (s2_dir,fname)
        # TODO data_flow
        model_of1 = "%s/model-%s.json" % (s2_dir,fname)
        print("fname: %s," % fname, end=' ')

        cmd = cmd_base + ["-pm-stage", str(stage), "-sr-input", sr_input,
            "-trace", trace_f, "-reg-acc", reg_acc_f,
            "-model-input", model_if, "-model-output", model_of1]
        if args.run_from_fs:
            cmd += ["-aflFile", args.afl_file]
        #print "cmd: %s" % ' '.join(cmd)

        ret_val = qemu_run(cmd, cfg.retry_num, stage)
        term_cond0[fname] = ret_val

    print('')
    return s2_dir, term_cond0


def stage2_2(srr_info, s2_dir, fname_l, inv_num=1):
    # update model.json for registers whose category is changed during PI

    global model_if, model_of
    # global depth, stage_str

    stage = 2.2
    # use same model_if with stage 2.0(i.e. model_of of stage 1.5)
    model_if = model_if
    model_of = "model-depth:%s,stage:%.1f.json" % (depth,stage)
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    if inv_num > 1:
        color_print("\tinvocation number: %d" % inv_num, "blue")
        model_of = "model-depth:%s,invoc:%d,stage:%.1f.json" % \
          (depth,inv_num,stage)

    # summarize register type updated(new categorized reg+recategorized reg)
    '''
    rc_diff = {reg_idx: {old: {type:, r/w/:, attr:}, new: [{type:, r/w:, attr:, fname:[]}]}}
    rc_diff is complex as for same old, there could be multiple new,
    for same new, there might be multiple fname
    '''
    rc_diff = {}
    model = json.load(open(model_if))
    # XXX only check rc changed for current peri. It's very rare that rc of
    # other peri is changed during worker run
    old = model["model"][srr_info.peri_ba]["regs"] # old is dumped
    for fname_b in fname_l:
      fname=fname_b.decode()
      model_of1 = "%s/model-%s.json" % (s2_dir, fname)
      new = json.load(open(model_of1))["model"][srr_info.peri_ba]["regs"]
      # extend old to the same length with new
      for i in range(len(old), len(new)):
        old.append({"type": 0})
      for i in range(0, len(new)):
        # don't consider cr_value when cmp two reg cat
        old[i].pop("cr_value", None)
        new[i].pop("cr_value", None)
        if cmp(old[i], new[i]) != 0: # not equal
          if i not in rc_diff:
            new[i]["fname"] = [fname]
            rc_diff[i] = {"old": old[i], "new": [new[i]]}
          else:
            # we don't change old as it is always the same
            found_same_new = False
            for n in rc_diff[i]["new"]:
              # don't consider fname when cmp two reg cat
              n2 = dict(n)
              del n2["fname"]
              if cmp(n2, new[i]) == 0: # equal
                n["fname"].append(fname)
                found_same_new = True
                break

            if found_same_new is False:
              new[i]["fname"] = [fname]
              rc_diff[i]["new"].append(new[i])

    color_print("Register category changed in PI: ")
    json.dump(rc_diff, sys.stdout, indent=4)
    #print rc_diff

    # update reg cat in json
    for k in rc_diff:
      new_types = set([i["type"] for i in rc_diff[k]["new"]])
      if len(new_types) > 1:
        color_print("Multiple new categories are assigned to: %d"%k, "red")
        print("Category not updated")
      else:
        # XXX assume sr_locked can be changed from 0 to 1, not the other way
        r,w,sr_locked = 0,0,0
        new_type = new_types.pop()
        for i in rc_diff[k]["new"]:
          if i["read"]:
            r = i["read"]
          if i["write"]:
            w = i["write"]
          if new_type in [2, 4] and i["sr_locked"]:
            sr_locked = i["sr_locked"]
        # upd cat by rewrite old[k]
        old[k] = {"type": new_type, "read": r, "write": w}
        if new_type in [2, 4]:
          old[k]["sr_locked"] = sr_locked

    # del model["sr_read"] # delete non-model fields
    json.dump(model, open(model_of, "w"), sort_keys=True, indent=4)

    print('')
    return


def stage2_4(srr_info, s2_dir, inv_num=1): # inv_num is unused
    # figure out what bits are checked by grouping execution trace by md5
    # if a bit is checked, execution trace differs when it is set or not set

    # global depth, stage_str

    stage = 2.4
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    set_bits = 2 if inv_num == 3 else 1

    # trace_sig = {"AB+CD":{nested dic}, "sig":sig, "summary":{sig:[[bits],]}}
    trace_sig = exec_trace_sig(srr_info.sr_bits, srr_info.sr_num, s2_dir, 
        b"trace-", set_bits)

    # driver_checked_bits: returns checked bit combinations to checked_bcs,
    # and inserts "checked_bits" key into trace_sig
    checked_bcs = driver_checked_bits(srr_info.sr_num, trace_sig, set_bits)
    # trace_sig is modified in driver_checked_bits, so dump here
    json.dump(trace_sig, open("%s/trace_summary.json" % s2_dir, "w"), indent=4)

    checked_bcs.sort()
    color_print("Bit combinations checked by driver: \n%s" % checked_bcs)

    print('')
    return checked_bcs, trace_sig


def stage2_5(srr_info, s2_dir, term_cond0, checked_bcs, trace_sig, inv_num=1):
    # infer bits functionality by reg_acc sequence

    global model_if, model_of
    # global depth, stage_str

    stage = 2.5
    model_if = model_of
    model_of = "model-depth:%s,stage:%.1f.json" % (depth,stage)
    color_print("depth %d, stage: %s" % (depth, stage_str[stage]), "blue")

    model = json.load(open(model_if))

    set_bits = 2 if inv_num == 3 else 1

    # all arrays in bit_func and prereq is created with ["bit:ab,bit:cd"],
    # then converted to [(SRx_BITac, set-1, SRy_BITcd, set-1),], BIT may be -1
    # then all -1 is removed, along which clear-0 may be introduced
    bit_func = {
      "rx": [],
      "tx": [],
      "error": [], # needs to be cleared
      "unknown": [], # checked but func. unknown(e.g. USART_TXE when TX disabled)
    }
    prereq = {
      "satisfy": [], # Rx, Tx, Ready(set), Busy(clear)
      "never_satisfy": [], # Error
      "other": [], # all other combinations
    }

    # preprocess feedbacks
    fname_l2 = [] # contains only fname that corresponds to checked bits
    reg_acc_dic = {}
    bbl_cov_dic = {}
    term_cond = {}
    for checked_bc in checked_bcs:
        # checked_bc = [[int(ab), int(cd)], [int(AB)]] -> fname = 'bit:ab+cd,bit:AB'
        fname = ""
        for checked_b in checked_bc:
            fname += "bit:%s," % '+'.join([str(x).zfill(2) for x in checked_b])
        fname = fname[:-1] # trim last ','
        fname_l2.append(fname)
        #color_print(fname)

        # reg_acc_dic = {fname: reg_acc_l}
        reg_acc_f = "%s/reg_acc-%s" % (s2_dir,fname)
        # reg_acc_l = [(addr, type, r/w, val, bbl_s, bbl_e)]
        reg_acc_l = re.findall((r"\((0x[0-9a-f]+), ([0-9]), ([rw]), "
            r"([0-9a-f]+)\) in BBL \((0x[0-9a-f]+), (0x[0-9a-f]+)\)"),
            open(reg_acc_f).read())
        reg_acc_dic[fname] = reg_acc_l

        # bbl_cov_dic = {fname: bbl_cov}
        trace_f = "%s/trace-%s" % (s2_dir,fname)
        bbl_cov_dic[fname] = cnt_bbl_cov(trace_f)

        # term_cond0 = {fname: ret_val} -> term_cond = {ret_val: [fname]}
        ret_val = term_cond0[fname]
        if ret_val not in term_cond:
            term_cond[ret_val] = [fname]
        else:
            term_cond[ret_val].append(fname)

    #print fname_l2
    color_print("term_cond for checked bits: ")
    print(term_cond)


    # infer bit functionality per term_cond
    if set(term_cond.keys()) == set([0x23, 0x21]):
        color_print("hang + func_ret")
        if len(trace_sig["checked_bits"]) > 1:
          color_print("# of checked bits = %d > 1, code is never tested "
            "under this circumstance" % len(trace_sig["checked_bits"]), "red")

        # append bit combination that moves forward
        prereq["satisfy"].append(term_cond[0x21][0])


    if set(term_cond.keys()) == set([0x23]):
        color_print("hang only")
        '''
        multiple hangs exhibit diff exec trace
        case 1: hangs at different places: one moves forward and hangs there, 
        the other doesn't move forward and hangs in-place.
        We infer the condition to move forward.
        case 2: all cases move forward and hang there, 
        diff exec trace is caused by true/false branch execution. 
        We infer the condition for rx/tx, and avoid error.
        case 3: all cases don't move forward and hang in-place. 
        e.g. wait for multi-bits being set simultaneously.
        Never seen and not handled. 
        '''
        if len(trace_sig["checked_bits"]) > 1:
          color_print("# of checked bits = %d > 1, code is never tested"
            "under this circumstance" % len(trace_sig["checked_bits"]), "red")

        # tell it is case 1 or 2 by comparing last srr_site with srr_bbl_e
        # case 1: last srr_site is the same to srr_bbl_e in one case, 
        #         but different in the other
        # case 2: last srr_site are the same in both cases and differs from srr_bbl_e

        # term_srr_site = {srr_site: [fname]}
        # srr_site is represented by hex(bbl_e)
        term_srr_site = {}
        for fname in fname_l2:
          last_srr_site = reg_acc_dic[fname][-1][5]
          if last_srr_site not in term_srr_site:
            term_srr_site[last_srr_site] = [fname]
          else:
            term_srr_site[last_srr_site].append(fname)
        color_print("term_srr_site: ")
        print(term_srr_site)

        if len(term_srr_site) == 2 and srr_info.srr_bbl_e in term_srr_site:
          # case 1, prefer bit combination that moves forward
          del term_srr_site[srr_info.srr_bbl_e]
          prereq["satisfy"].append(list(term_srr_site.values())[0][0])
          print(prereq)

        elif len(term_srr_site) == 1:
          # case 2, decided to set/clear the checked bit by its functionality
          # figure out the input that covers more new bbl
          cov = []
          for fname in fname_l2:
            #color_print(fname)

            # summarize new bbl covered
            new_cov = []
            for k in bbl_cov_dic[fname]:
                if k not in bbl_cov:
                    new_cov.append(k)
            cov.append(new_cov)

          color_print("new bbl covered:")
          print(cov)

          fname_idx = cov.index(max(cov))
          most_new_bbl_f = fname_l2[fname_idx]
          color_print("most_new_bbl_f:")
          print(most_new_bbl_f)

          reg_acc_l = [(ra[1], ra[2]) for ra in reg_acc_dic[most_new_bbl_f]]
          if ('2', 'w') in reg_acc_l:
            # it is error bits since we see SR write
            # TODO other patterns to identify error bits
            prereq["never_satisfy"].append(fname)
          else:
            prereq["satisfy"].append(fname)

        else:
          color_print("Unhandled cases for hang + func_ret!", "red")


    if set(term_cond.keys()) == set([0x21]):
        color_print("func_ret only")

        regs = model["model"][srr_info.peri_ba]["regs"]
        for fname in fname_l2:
            color_print(fname)

            DR_r,DR_w = 0,0
            # reg_acc_dic = {fname: [(addr, type, r/w, val, bbl_s, bbl_e)]}
            for ra in reg_acc_dic[fname]:
                # TODO handle multi-SR
                if ra[1] == '2' and ra[2] == 'r':
                    # Upon a SR_r, lifetime of value of previous SR_r ends
                    # So we don't infer bits' functionality for previous SR_r upon a new SR_r
                    break

                reg_offset = int(ra[0], 16) - int(srr_info.peri_ba, 16)
                if reg_offset < 0 or reg_offset >= cfg.peri_addr_range:
                  # reg accessed doesn't belong to the same peri which triggered this SRR
                  continue
                reg_idx = int(reg_offset * 8 / srr_info.sr_bits)
                #print "ra: %s, reg_idx: %d" % (ra, reg_idx)
                if ra[1] == '3' and regs[reg_idx]["type"] == 3:
                    # DR and is still DR at the end of current run
                    if ra[2] == 'r':
                        DR_r += 1
                    else:
                        DR_w += 1

            print("DR_r: %d, DR_w: %d" % (DR_r, DR_w))
            if DR_r > 0:
                bit_func["rx"].append(fname)
            if DR_w > 0:
                bit_func["tx"].append(fname)
            if (DR_r + DR_w) > 1:
                color_print("DR_r+DR_w: %d > 1!" % (DR_r+DR_w), "red")
            print('')

        prereq["satisfy"].extend(bit_func["rx"])
        prereq["satisfy"].extend(bit_func["tx"])
        prereq["never_satisfy"].extend(bit_func["error"])


    # fill in other array
    for fname in fname_l2:
        if fname not in prereq["satisfy"] and \
          fname not in prereq["never_satisfy"]:
          # TODO remove dup tup when single bit is checked under single SR
          prereq["other"].append(fname)


    # both array: ['bit:ab+cd,bit:AB',] -> 
    # [[[int(ab), s/c, int(cd), s/c], [int(AB), s/c]]]
    # Convert -1 so it doesn't appear in ab/cd. 
    # If cannot convert, delete it and also signal error for satisfy
    for k,v in list(prereq.items()):
        #color_print("%s: %s" % (k,v))

        # b: bits, bc: bit_combination, str: type is string instead of int
        v1 = []
        for fname in v:
            # fname = 'bit:ab+cd,bit:AB' -> bc = [[int(ab),int(cd)], [int(AB)]]
            # ab/cd can be -1
            bc = [list(map(int, b.strip("bit:").split('+'))) for b in fname.split(',')]

            # bc = [[-1,-1], [int(cd)]] -> tup = [[cb1,cb2], c, [int(cd)], s]
            tup = []
            convert_ok = True
            for i in range(0, len(bc)):
              b = bc[i]
              if b == [-1]*set_bits:
                # figure out what bits are checked for current SR
                # under bit combinations of previous SR
                cb_dic = trace_sig
                for j in range(0, i):
                  # [int(ab), int(cd)] -> "ab+cd"
                  cb_dic_k = '+'.join([str(x).zfill(2) for x in tup[j*2]])
                  cb_dic = cb_dic[cb_dic_k]

                if len(cb_dic["checked_bits"]) == 1:
                  # single checked bit
                  tup.append(cb_dic["checked_bits"][0])
                  tup.append(0)
                elif len(cb_dic["checked_bits"]) == 0:
                  #convert_ok = False
                  #color_print("no bits are checked, empty prerequisite "
                  #  "set!", "yellow")

                  # (-1, clear) means no bit is checked
                  # TODO detect no-checked bit SR under multi-SR
                  tup.append([-1]*set_bits)
                  tup.append(0)
                else:
                  # multiple checked bits
                  convert_ok = False
                  if k == "other":
                    # For other, delete if cannot convert
                    break

                  # For satisfy/never_satisfy, signal err if cannot convert
                  color_print("-1 appears when multiple bits are checked, "
                    "we cannot handle it currently!", "red")
                  #if single-SR:
                    # not implemented since it is never seen
                    #convert -1 to never_satisfy
                  #else:
                    # current prereq formulation cannot handle this case
              else:
                # not -1, don't have to do conversion
                tup.append(b)
                tup.append(1)

            if convert_ok:
              v1.append(tup)
            # end of "for i in range(0, len(bc)):"

        prereq[k] = v1
        # end of "for fname in v:"

    # end of "for k,v in prereq.items():"

    color_print("prereq: %s" % prereq)
    print('')


    # dump events into json
    evts = model["model"][srr_info.peri_ba]["events"]
    if srr_info.CR_val not in evts:
        evts[srr_info.CR_val] = {}
    conf = evts[srr_info.CR_val]

    if srr_info.srr_bbl_e in conf:
        # TODO move this warning to the end of stage 1
        color_print("We have done PI on this BBL before!", "yellow")
    else:
        conf[srr_info.srr_bbl_e] = {}
    srr_site = conf[srr_info.srr_bbl_e]

    srr_site["sr_num"] = srr_info.sr_num
    srr_site["sr_idx"] = srr_info.sr_idx
    srr_site["set_bits"] = set_bits

    if srr_info.CR_SR_r_idx:
      # if > 0 (checked at stage2_5), CR_SR (at most 1) requires SMR
      srr_site["CR_SR_r_idx"] = srr_info.CR_SR_r_idx

    for k,v in list(prereq.items()):
        if k in srr_site:
            srr_site[k] += v
        else:
            srr_site[k] = v

    if args.eval:
        srr_site["srr_func"] = srr_info.srr_func
        srr_site["bbl_cnt"] = srr_info.srr_bbl_cnt

    json.dump(model, open(model_of, "w"), indent=4)

    print('')
    return



if __name__ == "__main__":
    start_time = time.time()

    parser = argparse.ArgumentParser(description="Model Extraction Tool")
    # args must be processed before chdir to run_num
    parser.add_argument("--model-if", dest="model_if", default=None, 
        help="model input file")
    parser.add_argument("-c", "--config", dest="config", 
        default="%s/fuzz.cfg" % os.path.dirname(sys.argv[0]),
        help="configuration file. Default: fuzz.cfg")
    parser.add_argument("-g", "--ground-truth", dest="gt", default=None,
        help="ground truth file in csv format")
    # args processed after chdir to run_num
    parser.add_argument("--run-num", dest="run_num", default="1", 
        help="number of run")
    parser.add_argument("-f", "--print-to-file", dest="print_to_file", 
        action='store_true', help="redirect stdout to file named stdout")

    # evaluation
    parser.add_argument("-e", "--evaluation", dest="eval", 
        action='store_true', help="switch to turn on evaluation only operations")

    fs = parser.add_argument_group("arguments for run from forkserver")
    fs.add_argument("--run-from-forkserver", dest="run_from_fs", 
        action="store_true", help="if is invoked from forkserver during fuzzing")
    fs.add_argument("--afl-file", dest="afl_file", default=None, 
        help="fuzzer generated input file for DR read")
    args = parser.parse_args()

    if args.model_if:
        args.model_if = os.path.abspath(args.model_if)

    cfg = read_config(args.config)

    if args.gt:
        args.gt = os.path.abspath(args.gt)
        try:
          csv.Sniffer().sniff(open(args.gt, 'rb').read(1024))
        except:
          sys.exit("ground truth file %s is not a valid csv file" % args.gt)

    if not os.path.exists(args.run_num):
        os.makedirs(args.run_num)
    color_print("Change working dir to: %s/" % args.run_num)
    os.chdir(args.run_num)
    print("CWD: %s" % os.getcwd())

    if args.print_to_file:
        color_print("Redirect stdout to file named stdout")
        sys.stdout = open("stdout", 'w')
        #sys.stderr = open("stderr", 'w')
        shutil.copyfile(cfg.img, "firmware.elf")

    if args.run_from_fs:
        # log invocation of me alg
        logging.basicConfig(filename=cfg.log_f, level=logging.INFO, 
          format='[%(asctime)s] %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')
        model = json.load(open(args.model_if))
        logging.info("run_num %s, access_to_unmodeled_peri: %s" % 
          (args.run_num, model["access_to_unmodeled_peri"]))

        # copy aflFile
        shutil.copyfile(args.afl_file, "aflFile")


    print("cmd to launch this script: %s\n" % ' '.join(sys.argv))
    print("args after processing: %s\n" % args)
    print("configurations after processing: %s\n" % cfg)

    signal.signal(signal.SIGTERM, sig_handler)
    atexit.register(exit_callback)

    depth = 0

    stage_str = {
        0: "INVALID", 
        # the following line must be consistent w/ qemu's def
        1: "SR_R_ID", 2: "SR_R_EXPLORE", 3: "FUZZING",
        # rerun qemu when stage 1 is terminated by SR_cat_by_fixup
        1.1: "rerun SR_R_ID, since it is terminated by SR_cat_by_fixup",
        # below is py only
        1.4: "identify and handle registers adjusted to/from CR/SR",
        1.5: "collect info for SR read site",
        1.9: "prepare input for SR_R_EXPLORE",
        2.2: "identify and handle register category changes during SR_R_EXPLORE",
        2.4: "identify bits checked by driver at this srr",
        2.5: "infer functionality of each checked bit",
    }

    cmd_base = [cfg.qemu_bin, "-verbose", "-verbose", "-d", cfg.qemu_log, "-nographic",
            "-board", cfg.board, "-mcu", cfg.mcu, "-image", cfg.img]

    # bbl_cov = {(hex_str(bbl_s), hex_str(bbl_e)): cnt}
    bbl_cov = {} # reset when ME restart due to e.g. cr_ins

    # rc_adjusted_sum = {"depth:%s,stage:%.1f": rc_adjusted}
    rc_adjusted_sum = {}

    last_peri_model = args.model_if


    while True:
        depth += 1

        ret_val = stage1()
        if ret_val == 0x19:
            stage1(redo=True)


        cr_ins_happen = stage1_4()
        if cr_ins_happen:
            bbl_cov = {} # reset when ME restart due to e.g. cr_ins
            continue

        # do assignment here rather than after stage 1 to guarantee adjusted SMR
        last_peri_model = model_of

        if ret_val == 0x30:
            color_print("QEMU hasn't seen unmodeled SR_r site for a while, "
              "terminate model extraction alg!\nPossible reasons: hang caused "
              "by an error or we have extracted all possible model.", "blue")
            sys.exit()


        srr_info = stage1_5()

        inv_num = 1
        (sr_dir, fname_l) = stage1_9(srr_info)
        (s2_dir, term_cond0) = stage2(srr_info, sr_dir, fname_l)
        stage2_2(srr_info, s2_dir, fname_l)
        (checked_bcs, trace_sig) = stage2_4(srr_info, s2_dir)

        if checked_bcs == [[[-1]]]: # TODO support multi SR
          color_print("No bits are checked! Run stage 2 again with "
            "workers terminates after TWO func_ret or 'timeout'", "yellow")
          inv_num = 2

          (s2_dir, term_cond0) = stage2(srr_info, sr_dir, fname_l, inv_num)
          stage2_2(srr_info, s2_dir, fname_l, inv_num)
          (checked_bcs, trace_sig) = stage2_4(srr_info, s2_dir, inv_num)

          if checked_bcs == [[[-1]]]:
            color_print("Still no bits are checked! Run stage 2 again with "
              "inputs setting TWO bits, and workers terminates after ONE "
              "func_ret", "yellow")
            inv_num = 3

            (sr_dir, fname_l) = stage1_9(srr_info, inv_num)
            (s2_dir, term_cond0) = stage2(srr_info, sr_dir, fname_l, inv_num)
            stage2_2(srr_info, s2_dir, fname_l, inv_num)
            (checked_bcs, trace_sig) = stage2_4(srr_info, s2_dir, inv_num)

            if checked_bcs == [[[-1]*2]]:
              color_print("Still no bits are checked! It may not be a SR!", 
                "yellow")
              # TODO multi SR
              model = json.load(open(model_of))
              peri = model["model"][srr_info.peri_ba]
              reg = peri["regs"][srr_info.sr_idx[0]]

              if not reg["sr_locked"]:
                color_print("SR is not locked, adjust it to DR", "yellow")
                reg["type"] = 3

                # handle sr_del
                handle_sr_cr_del(peri["events"], 
                  {"cr_del":[], "sr_del":[srr_info.sr_idx[0]]})

                json.dump(model, open(model_of, "w"), indent=4)
                color_print("depth %d done!\n" % depth, "blue")
                continue

              else:
                color_print("SR is locked! Don't adjust it to DR", "yellow")


        stage2_5(srr_info, s2_dir, term_cond0, checked_bcs, trace_sig, inv_num)
        last_peri_model = model_of

        color_print("depth %d done!\n" % depth, "blue")
