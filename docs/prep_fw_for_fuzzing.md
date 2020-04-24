## Preparing firmware for fuzzing
### Invoking `startForkserver` aflCall
P<sup>2</sup>IM inherits `startForkserver` aflCall from TriforceAFL, 
although the current implementation does not use the fork server feature of AFL.

The firmware has to explicitly invoke `startForkserver` aflCall.

Please paste the following snippet to the file in which you are going to invoke `startForkserver`.
```c
#include <stdint.h>

int noHyperCall = 0; // 1: don't make hypercalls

__attribute__ ((naked)) uint32_t aflCall(__attribute__ ((unused)) uint32_t a0, __attribute__ ((unused)) uint32_t a1, __attribute__ ((unused)) int32_t a2) {
    /*
     * In qemu, svc $0x3f is intercepted, without being executed
     * On real device, it is executed and may cause firmware crash
     * It can be skipped by set noHyperCall to 1
     */
    __asm__ __volatile__ ("svc $0x3f\n\t"
                          "bx %lr\n\t");
}

int startForkserver(int ticks) {
    if(noHyperCall)
        return 0;
    return aflCall(1, ticks, 0);
}

```

Then invoke `startForkserver` by 
```c
startForkserver(0);
```

It does not matter where the aflCall is invoked. 
You can invoke it either before executing the first instruction, or after the firmware boots up.
We plan to remove the requirement of invoking `startForkserver` aflCall in the future.


### Build firmware in debug mode (optional)
Crash triage (i.e., analyzing crashing/hanging test cases) is way easier with debug symbols.
You can build firmware in debug mode by, for example, `-g3 -ggdb` option of `arm-none-eabi-gcc`.
Some ELF file is also stripped in the build process. 
Please disable that to preserve the debug symbols.
This is an optional step, which is not required to fuzz the firmware.
