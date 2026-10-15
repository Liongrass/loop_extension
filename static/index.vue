<template id="page-loop">
  <div class="row q-col-gutter-md">
    <div class="col-12 col-md-5">
      <q-card>
        <q-card-section>
          <h5 class="text-subtitle1 q-my-none">Loop Out</h5>
          <p class="text-caption q-mb-none">
            Swap Lightning balance to an on-chain Bitcoin address.
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
            ></q-input>
            <q-input
              filled
              dense
              v-model.trim="form.address"
              type="text"
              label="Bitcoin address *"
            ></q-input>
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

    <template v-if="isAdmin">
      <div class="col-12">
        <q-card>
          <q-card-section class="row items-center no-wrap">
            <div class="col">
              <h5 class="text-subtitle1 q-my-none">Admin: Loop via litd</h5>
            </div>
            <q-btn
              unelevated
              color="primary"
              :loading="loading"
              @click="fetchLoop"
            >
              Refresh
            </q-btn>
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6">
        <q-card>
          <q-card-section>
            <h6 class="text-subtitle2 q-my-none">loop getinfo</h6>
            <pre
              class="q-mb-none"
              style="white-space: pre-wrap; word-break: break-all"
              v-text="info ? pretty(info) : '—'"
            ></pre>
          </q-card-section>
        </q-card>
      </div>
      <div class="col-12 col-md-6">
        <q-card>
          <q-card-section>
            <h6 class="text-subtitle2 q-my-none">loop terms</h6>
            <pre
              class="q-mb-none"
              style="white-space: pre-wrap; word-break: break-all"
              v-text="terms ? pretty(terms) : '—'"
            ></pre>
          </q-card-section>
        </q-card>
      </div>
    </template>

    <q-dialog v-model="quoteDialog.show">
      <q-card v-if="quote" class="q-pa-lg lnbits__dialog-card">
        <h6 class="text-subtitle1 q-mt-none q-mb-md">Confirm Loop Out</h6>
        <q-markup-table dense flat>
          <tbody>
            <tr>
              <td>To</td>
              <td class="text-right" style="word-break: break-all">
                <span v-text="form.address"></span>
              </td>
            </tr>
            <tr>
              <td>Amount</td>
              <td class="text-right">
                <span v-text="sat(quote.amount)"></span>
              </td>
            </tr>
            <tr>
              <td>Swap fee</td>
              <td class="text-right">
                <span v-text="sat(quote.swap_fee)"></span>
              </td>
            </tr>
            <tr>
              <td>
                Routing fees (max)
                <div class="text-caption">
                  Prepay of <span v-text="sat(quote.prepay_amount)"></span> is
                  part of the swap fee
                </div>
              </td>
              <td class="text-right">
                <span
                  v-text="
                    sat(quote.max_swap_routing_fee + quote.max_prepay_routing_fee)
                  "
                ></span>
              </td>
            </tr>
            <tr class="text-weight-bold">
              <td>Taken from wallet (max)</td>
              <td class="text-right">
                <span v-text="sat(quote.max_total)"></span>
              </td>
            </tr>
            <tr>
              <td>
                Miner fee (estimate)
                <div class="text-caption">
                  Deducted on-chain, up to
                  <span v-text="sat(quote.max_miner_fee)"></span>
                </div>
              </td>
              <td class="text-right">
                <span v-text="sat(quote.miner_fee_estimate)"></span>
              </td>
            </tr>
            <tr class="text-weight-bold">
              <td>You receive on-chain (about)</td>
              <td class="text-right">
                <span
                  v-text="sat(quote.amount - quote.miner_fee_estimate)"
                ></span>
              </td>
            </tr>
          </tbody>
        </q-markup-table>
        <p class="text-caption q-mt-md q-mb-none">
          Unused fees are returned when the swap completes. Swaps can take a
          while: the on-chain transaction has to confirm first.
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
