<template id="page-loop">
  <div class="row q-col-gutter-md">
    <div class="col-12 col-md-5">
      <q-card>
        <q-card-section>
          <h5 class="text-subtitle1 q-my-none">Loop Out</h5>
          <p class="text-caption q-mb-none">
            Swap Lightning balance to an on-chain Bitcoin address.
          </p>
          <p v-if="terms" class="text-caption q-mb-none">
            Amount between
            <span v-text="sat(terms.min_swap_amount)"></span> and
            <span v-text="sat(terms.max_swap_amount)"></span>.
          </p>
        </q-card-section>
        <q-card-section>
          <q-form @submit="getQuote" class="q-gutter-md">
            <q-select
              filled
              dense
              emit-value
              map-options
              v-model="form.wallet"
              :options="g.user.walletOptions"
              label="Wallet *"
            ></q-select>
            <q-input
              filled
              dense
              v-model.number="form.amount"
              type="number"
              min="1"
              label="Amount (sat) *"
              hint="Arrives on-chain, minus the miner fee"
              :rules="[amountRule]"
            ></q-input>
            <q-input
              filled
              dense
              v-model.trim="form.address"
              type="text"
              label="Bitcoin address *"
            ></q-input>
            <q-input
              filled
              dense
              v-model.number="form.conf_target"
              type="number"
              min="2"
              max="100"
              label="Confirmation target (blocks)"
              hint="How quickly the on-chain sweep should confirm"
              :rules="[confTargetRule]"
            ></q-input>
            <q-checkbox
              dense
              v-model="form.fast"
              label="Fast"
            ></q-checkbox>
            <div class="text-caption q-mt-none">
              Without fast, the swap server may wait up to 30 minutes before
              publishing, for a lower fee.
            </div>
            <q-btn
              unelevated
              color="primary"
              type="submit"
              :loading="quoting"
              :disable="!form.wallet || !form.amount || !form.address"
            >
              Get quote
            </q-btn>
          </q-form>
        </q-card-section>
      </q-card>
    </div>

    <div class="col-12 col-md-7">
      <q-card>
        <q-card-section class="row items-center no-wrap">
          <div class="col">
            <h5 class="text-subtitle1 q-my-none">Your swaps</h5>
          </div>
          <q-btn flat dense icon="refresh" @click="getSwaps"></q-btn>
        </q-card-section>
        <q-table
          dense
          flat
          :rows="swaps"
          :columns="swapsTable.columns"
          row-key="id"
          no-data-label="No swaps yet"
          v-model:pagination="swapsTable.pagination"
        >
          <template v-slot:body-cell-status="props">
            <q-td :props="props">
              <q-badge
                :color="statusColor(props.row.status)"
                v-text="props.row.status"
              ></q-badge>
              <div
                v-if="props.row.failure_reason"
                class="text-caption text-negative"
                style="white-space: normal"
                v-text="props.row.failure_reason"
              ></div>
            </q-td>
          </template>
        </q-table>
      </q-card>
    </div>

    <div v-if="isAdmin" class="col-12">
      <q-card>
        <q-card-section class="row items-center no-wrap">
          <div class="col">
            <h5 class="text-subtitle1 q-my-none">Loop daemon</h5>
            <div class="text-caption">Only visible to admins</div>
          </div>
          <q-btn
            flat
            color="primary"
            :loading="loadingInfo || loadingAllSwaps"
            :disable="!showInfo && !showAllSwaps"
            @click="refreshAdmin"
          >
            Refresh
          </q-btn>
          <q-btn
            unelevated
            color="primary"
            class="q-ml-sm"
            :icon-right="showInfo ? 'expand_less' : 'expand_more'"
            @click="toggleInfo"
          >
            Get info
          </q-btn>
          <q-btn
            unelevated
            color="primary"
            class="q-ml-sm"
            :icon-right="showAllSwaps ? 'expand_less' : 'expand_more'"
            @click="toggleAllSwaps"
          >
            All swaps
          </q-btn>
        </q-card-section>
        <q-slide-transition>
          <q-card-section v-show="showInfo">
            <pre
              class="q-my-none"
              style="white-space: pre-wrap; word-break: break-all"
              v-text="info ? pretty(info) : '—'"
            ></pre>
          </q-card-section>
        </q-slide-transition>
        <q-slide-transition>
          <div v-show="showAllSwaps">
            <q-table
              dense
              flat
              :rows="allSwaps"
              :columns="allSwapsColumns"
              row-key="id"
              no-data-label="No swaps yet"
              v-model:pagination="allSwapsPagination"
            >
              <template v-slot:body-cell-wallet="props">
                <q-td :props="props">
                  <div v-text="props.row.wallet_name || '(deleted wallet)'"></div>
                  <div class="text-caption" v-text="props.row.wallet"></div>
                </q-td>
              </template>
              <template v-slot:body-cell-status="props">
                <q-td :props="props">
                  <q-badge
                    :color="statusColor(props.row.status)"
                    v-text="props.row.status"
                  ></q-badge>
                  <div
                    v-if="props.row.failure_reason"
                    class="text-caption text-negative"
                    style="white-space: normal"
                    v-text="props.row.failure_reason"
                  ></div>
                </q-td>
              </template>
            </q-table>
          </div>
        </q-slide-transition>
      </q-card>
    </div>

    <q-dialog v-model="quoteDialog.show">
      <q-card v-if="quote" class="q-pa-lg lnbits__dialog-card">
        <h6 class="text-subtitle1 q-mt-none q-mb-md">Confirm Loop Out</h6>
        <div
          class="q-mb-md"
          style="
            display: grid;
            grid-template-columns: 1fr auto;
            column-gap: 24px;
            row-gap: 12px;
            font-variant-numeric: tabular-nums;
          "
        >
          <div>You pay over Lightning</div>
          <div
            class="text-right"
            v-text="sat(quote.amount + quote.swap_fee)"
          ></div>

          <div>Estimated swap fee</div>
          <div
            class="text-right"
            v-text="sat(quote.swap_fee + quote.miner_fee_estimate)"
          ></div>

          <q-separator style="grid-column: 1 / -1"></q-separator>

          <div class="text-weight-bold">You receive on-chain</div>
          <div
            class="text-right text-weight-bold"
            v-text="sat(quote.amount - quote.miner_fee_estimate)"
          ></div>
        </div>
        <p class="text-caption q-mt-lg q-mb-none">
          Swaps can take a while: the on-chain transaction has to confirm first.
        </p>
        <div class="row q-mt-lg">
          <q-btn
            unelevated
            color="primary"
            :loading="starting"
            @click="startLoopOut"
          >
            Loop Out
          </q-btn>
          <q-btn v-close-popup flat color="grey" class="q-ml-auto">
            Cancel
          </q-btn>
        </div>
      </q-card>
    </q-dialog>
  </div>
</template>
