This tutorial shows how to add a new MCU and board to QEMU. 
In short, you need to add the code template below into QEMU source code, and compile QEMU following instructions in [build_qemu.md](build_qemu.md). 

When you add the template, please replace everything enclosed in `${...}`. \
`${BOARD_NAME}` is the board name you want to use in `-board` option when launching QEMU. \
`${BOARD_DESCRIPTION}` is the message to be shown in `./qemu-system-gnuarmeclipse -board help`. \
`${MCU_NAME}` is the mcu name you want to use in `-mcu` option when launching QEMU. \
`${FLASH_BASE_ADDR}` is flash base address. \
`${FLASH_SIZE}` is flash size in kb. \
`${RAM_REGION1_BASED_ADDR}` is ram base address. \
`${RAM_REGION1_SIZE}` is ram size in kb. \

The template is based on a STM32 MCU. However, it can be used for any MCUs from any vendors because only those enclosed in `${...}` are actually used by QEMU and P<sup>2</sup>IM. 


In hw/arm/stm32-boards.c file, add
```c
/* ----- ${BOARD_NAME} ----- */
static void ${BOARD_NAME}_board_init_callback(MachineState *machine)
{
    cm_board_greeting(machine);

    {
        /* Create the MCU */
        Object *mcu = cm_object_new_mcu(machine, TYPE_${MCU_NAME});

        /* Set the board specific oscillator frequencies. */
        cm_object_property_set_int(mcu, 8000000, "hse-freq-hz"); /* 8.0 MHz */
        cm_object_property_set_int(mcu, 32768, "lse-freq-hz"); /* 32 kHz */

        cm_object_realize(mcu);
    }

    void *board_surface = cm_board_init_image("STM32F429I-Discovery.jpg",
            cm_board_get_desc(machine));

    Object *peripheral = cm_container_get_peripheral();
}

static QEMUMachine ${BOARD_NAME}_machine = {
    .name = "${BOARD_NAME}",
    .desc = "${BOARD_DESCRIPTION}",
    .init = ${BOARD_NAME}_board_init_callback };
```


In hw/arm/stm32-boards.c file, `static void stm32_machines_init(void)` function, add
```c
    qemu_register_machine(&${BOARD_NAME}_machine);
```


In hw/arm/stm32-mcus.c file, `static const STM32PartInfo stm32_mcus[]` array, add entry
```c
    {
        .name = TYPE_${MCU_NAME},
        .cortexm = {
            .flash_base = ${FLASH_BASE_ADDR}, // base address of flash
            .flash_size_kb = ${FLASH_SIZE}, // flash size in kb
            .sram_base = ${RAM_REGION1_BASED_ADDR}, // base address of ram
            .sram_size_kb = ${RAM_REGION1_SIZE}, // ram size in kb
            .sram_base2 = ${RAM_REGION2_BASED_ADDR}, // remove this line if the MCU has only one ram region
            .sram_size_kb2 = ${RAM_REGION2_SIZE}, // remove this line if the MCU has only one ram region
            .sram_base3 = ${RAM_REGION3_BASED_ADDR}, // remove this line if the MCU has only one or two ram region
            .sram_size_kb3 = ${RAM_REGION3_SIZE}, // remove this line if the MCU has only one or two ram region
            .core = &stm32f4_23_xxx_core,
        },
        .stm32 = &stm32f429xx

    },
```



In include/hw/arm/stm32-mcus.h file line 48, add
```c
#define TYPE_${MCU_NAME} "${MCU_NAME}"
```
