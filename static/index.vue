<template id="page-loop">
  <div v-if="isAdmin" class="row q-col-gutter-md">
    <div class="col-12">
      <q-card>
        <q-card-section class="row items-center no-wrap">
          <div class="col">
            <h5 class="text-subtitle1 q-my-none">Loop via litd</h5>
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
  </div>
</template>
