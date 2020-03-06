#include "peri-mod/peri-mod.h"
#include "peri-mod/interrupt.h"

void pm_enable_interrupt(int excp_num) {
    int i;
    for (i = 0; i < pm_interrupt->arr_size; i ++) {
      // exception number of NVIC instead of GIC is stored
      if (pm_interrupt->arr[i].int_num == excp_num) {
        pm_interrupt->arr[i].enabled = 1;
        break;
      }
    }

    if (i == pm_interrupt->arr_size) {
      // not found in arr, insert
      if (pm_interrupt->arr_size >= PM_MAX_INT_EN_NUM) {
        qemu_log_mask(CPU_LOG_INT, "bbl_cnt %d: pm_interrupt is full,"
          " cannot enable IRQ %d\n", bbl_cnt, excp_num-16);
        exit(0x77);
      } else {
        // Store exception number of NVIC instead of GIC
        pm_interrupt->arr[pm_interrupt->arr_size].int_num = excp_num;
        pm_interrupt->arr[pm_interrupt->arr_size ++].enabled = 1;

        qemu_log_mask(CPU_LOG_INT, "bbl_cnt %d: Enabled IRQ %d\n",
          bbl_cnt, excp_num-16);
      }
    }
}

void pm_disable_interrupt(int excp_num) {
    int i;
    for (i = 0; i < pm_interrupt->arr_size; i ++) {
      // exception number of NVIC instead of GIC is stored
      if (pm_interrupt->arr[i].int_num == excp_num) {
        pm_interrupt->arr[i].enabled = 0;

        qemu_log_mask(CPU_LOG_INT, "bbl_cnt %d: Disabled IRQ %d\n",
          bbl_cnt, excp_num-16);
        break;
      }
    }

    if (i == pm_interrupt->arr_size) {
      qemu_log_mask(CPU_LOG_INT, "bbl_cnt %d: Trying to disable a never "
        "enabled IRQ %d\n", bbl_cnt, excp_num-16);
      //exit(0x78);
    }
}

// Round robin interrupt firing
void pm_fire_interrupt(void) {
    int excp_num;
    int i, idx;

    for (i = 0; i < pm_interrupt->arr_size; i ++) {
      idx = (pm_interrupt->cur_int + i) % pm_interrupt->arr_size;
      if (idx == (pm_interrupt->arr_size - 1)) {
        // XXX During ME, there might be one interrupt fired INT_ROUND+1 times
        int_round ++;
      }

      if (pm_interrupt->arr[idx].enabled) {
        excp_num = pm_interrupt->arr[idx].int_num;
        cortexm_nvic_set_pending(pm_interrupt->s, excp_num);

        qemu_log_mask(CPU_LOG_INT, "bbl_cnt %d: Fired IRQ %d\n",
          bbl_cnt, excp_num-16);

        pm_interrupt->cur_int = (idx + 1) % pm_interrupt->arr_size;
        break;
      }
    }

    if (i == pm_interrupt->arr_size) {
      qemu_log_mask(CPU_LOG_INT, "bbl_cnt %d: No IRQ is enabled\n", bbl_cnt);
      int_round ++;
    }
}
