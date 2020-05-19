# P<sup>2</sup>IM: Scalable and Hardware-independent Firmware Testing via Automatic Peripheral Interface Modeling
## Directory structure
```
.
├── afl                           # fuzzer source code
├── docs                          # more documentation
├── externals                     # git submodules referencing external git repos for unit tests, real firmware, and ground truth
├── fuzzing
│   └── templates                 # "random" seeds and configuration file template to bootstrap fuzzing
├── LICENSE
├── model_instantiation           # scripts for instantiating processor-peripheral interface model and fuzzing the firmware
├── qemu
│   ├── build_scripts             # scripts for building QEMU from source code
│   ├── precompiled_bin           # pre-compiled QEMU binary for a quick start
│   └── src                       # QEMU source code. AFL and QEMU system mode emulation integration is based on TriforceAFL.
├── README.md
└── utilities
    ├── coverage                  # scripts for counting fuzzing coverage
    └── model_stat                # scripts for calculating statistics of the processor-peripheral interface model instantiated
```


## Setup
All steps have been tested on 64-bit Ubuntu 16.04.

### Cloning all git submodules
```bash
# submodules are cloned into externals/
git submodule update --init
```
git submodules are binded to a specific commit. Updates in submodules can be fetched by 
```bash
git submodule update --remote
```

### GNU Arm Embedded Toolchain
1. Download the toolchain from [here](https://developer.arm.com/tools-and-software/open-source-software/developer-tools/gnu-toolchain/gnu-rm/downloads).
2. Untar the downloaded file by `tar xjf *.tar.bz2`.
3. Add `bin/` directory extracted into your `$PATH` environment variable.
4. Test if the toolchain is added to `$PATH` successfully by `which arm-none-eabi-gcc`.

### AFL
```bash
# Compile AFL
make -C afl/
```

### QEMU
You can either use the [pre-compiled QEMU binary](qemu/precompiled_bin/), or build QEMU from source code following this [instruction](docs/build_qemu.md).


## Fuzzing
During fuzzing, P<sup>2</sup>IM instantiates processor-peripheral interface model on-demand (i.e., multiple rounds of model instantiation). The fuzzer-generated test cases are fed into the firmware when a DR is read.

The steps to fuzz a firmware by P<sup>2</sup>IM are as follows.

### Firmware preparation
You can fuzz one of the [10 real-world firmware](externals/) fuzz-tested in the P<sup>2</sup>IM paper, 
or prepare your own firmware for fuzzing following this [instruction](docs/prep_fw_for_fuzzing.md).

### Creating working directory
All data related to fuzzing is stored in the working directory.
```bash
WORKING_DIR=<repo_path>/fuzzing/<firmware_name>/<fuzzing_run_num>/
mkdir -p ${WORKING_DIR}
cd ${WORKING_DIR}
```
Then copy the firmware ELF file (instead of the .bin file) to the working directory.

### Preparing seed files
AFL requires a seed file to start. P<sup>2</sup>IM does not require any specific seed file (such as well-formated seeds).
We used a ["random" seed](fuzzing/templates/seeds/) when fuzz-tested the real-world firmware. 
```bash
# Copy the "random" seed to the working directory
cp -r <repo_path>/fuzzing/templates/seeds/ ${WORKING_DIR}/inputs
```

### Preparing the configuration file
A template for the configuration file is available [here](fuzzing/templates/fuzz.cfg.template)
```bash
# Copy the template to the working directory
cp <repo_path>/fuzzing/templates/fuzz.cfg.template fuzz.cfg
```
Please edit the configuration file following the instructions in the template.

### Launching fuzzer
Please make sure there is no previously instantiated model in `${WORKING_DIR}` before launching fuzzer.

```bash
<repo_path>/model_instantiation/fuzz.py -c fuzz.cfg
```


## Analyzing fuzzing results
### Result organization
```
.                                # working directory
├── ...
├── 0                            # round 0 of model instantiation. This is the first round, in which all-zero input is provided
│   ├── peripheral_model.json    # the model instantiated after this round
│   └── ...
├── 0.<seed_file_name>.<number>  # rounds of on-demand model instantiation triggered by seed inputs
│   ├── aflFile                  # input that triggers this round of model instantiation (here is the seed input)
│   ├── peripheral_model.json    # the model instantiated after this round
│   └── ...
├── <number>                     # Rounds of on-demand model instantiation triggered by fuzzer-generated inputs. <number> is any integer larger than 0.
│   ├── aflFile                  # fuzzer-generated input that triggers this round of model instantiation
│   ├── peripheral_model.json    # the model instantiated after this round
│   └── ...
├── ...
├── <firmware_elf>
├── fuzz.cfg
├── inputs                       # seeds required by AFL
├── me.log                       # log of on-demand model instantiation
└── outputs                      # AFL-generated test cases (they are inputs to the firmware fed by P2IM at DR read)
    ├── crashes                  # crashing test cases
    ├── fuzz_bitmap              # AFL coverage map
    ├── fuzzer_stats             # AFL statistics
    ├── hangs                    # hanging test cases
    ├── ...
    ├── queue                    # all test cases that lead to distinctive execution path
    └── run_fw.py                # helper script for running firmware in QEMU, with the instantiated model 
```
Order of model instantiation round: `0, 0.seed1.1, 0.seed1.2, ..., 0.seed1.m1, 0.seed2.1, ..., 0.seed2.m2, 1, 2, ..., n`. Round `n` is the `last_round_of_model_instantiation`.


### Calculating fuzzing coverage
```bash
cd ${WORKING_DIR}
<repo_path>/utilities/coverage/cov.py -c fuzz.cfg --model-if <last_round_of_model_instantiation>/peripheral_model.json
```
Coverage is output to `${WORKING_DIR}/coverage`, organized as follows:
```
coverage/
├── bbl_cnt                  # number of unique QEMU translation blocks executed
├── bbl_cov                  # execution frequency of each QEMU translation block. This is counted on all fuzzer-generated test cases
├── func_cov_merge_w_boot    # execution frequency of each instruction, grouped by functions. This is counted on all fuzzer-generated test cases
├── func_cov_w_boot          # function coverage
└── inst_cov_w_boot          # execution frequency of each instruction. This is counted on all fuzzer-generated test cases
```

### Calculating statistics of the instantiated processor-peripheral interface model 
```bash
# statFp3.py prints some statistics to stdout, some to stat.csv
<repo_path>/utilities/model_stat/statFp3.py <last_round_of_model_instantiation>/peripheral_model.json externals/p2im-ground_truth/<ground_truth_for_the_mcu> stat.csv
```
Documentation for `statFp3.py` can be found [here](utilities/model_stat/statFp3.py#L24).
Ground truth can be found [here](externals).

### Analyzing crashing/hanging input
`fuzz.py` automatically generates a helper script, `${WORKING_DIR}/run_fw.py`, for running test cases. The script runs firmware in QEMU using the instantiated model.

```bash
Usage: ./run_fw.py last_round_of_model_instantiation test_case [--debug]
       --debug argument is optional. It halts QEMU and wait for a debugger to be attached
```

To debug the firmware, do
```bash
# Run QEMU in debug mode 
./run_fw.py last_round_of_model_instantiation test_case --debug

# Attach gdb
arm-none-eabi-gdb -ex 'target remote localhost:9000' <firmware_elf>
```


## Running unit tests
Please refer to the documentation in `externals/p2im-unit_tests/README.md`


## More documentation
Please see [docs/](docs/) for more documentation.

Please refer to our [paper](https://www.usenix.org/conference/usenixsecurity20/presentation/feng) for more technical details of P<sup>2</sup>IM.


## Issues
If you encounter any problem while using our tool, please open an issue. 

For other communications, you can email feng.bo [at] husky.neu.edu.


## Citing our [paper](https://www.usenix.org/conference/usenixsecurity20/presentation/feng)
```bibtex
@inproceedings {p2im,
title = {P2IM: Scalable and Hardware-independent Firmware Testing via Automatic Peripheral Interface Modeling},
author={Feng, Bo and Mera, Alejandro and Lu, Long},
booktitle = {29th {USENIX} Security Symposium ({USENIX} Security 20)},
year = {2020},
url = {https://www.usenix.org/conference/usenixsecurity20/presentation/feng},
}
```
