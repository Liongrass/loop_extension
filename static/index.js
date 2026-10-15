window.PageLoop = {
  template: '#page-loop',
  data() {
    return {
      form: {wallet: null, amount: null, address: ''},
      quote: null,
      quoteDialog: {show: false},
      quoting: false,
      starting: false,
      swaps: [],
      swapsTable: {
        columns: [
          {
            name: 'created_at',
            label: 'Started',
            align: 'left',
            field: 'created_at',
            format: val => LNbits.utils.formatDate(val)
          },
          {
            name: 'amount',
            label: 'Amount',
            align: 'right',
            field: 'amount',
            format: val => this.sat(val)
          },
          {
            name: 'address',
            label: 'To',
            align: 'left',
            field: 'address',
            format: val => `${val.slice(0, 10)}…${val.slice(-6)}`
          },
          {name: 'status', label: 'Status', align: 'left', field: 'status'},
          {
            name: 'cost',
            label: 'Fees paid',
            align: 'right',
            format: (_, row) =>
              this.sat(row.cost_server + row.cost_onchain + row.cost_offchain)
          }
        ],
        pagination: {rowsPerPage: 10}
      },
      pollTimer: null,
      terms: null,
      info: null,
      showInfo: false,
      loadingInfo: false,
      allSwaps: [],
      allSwapsPagination: {rowsPerPage: 10},
      showAllSwaps: false,
      loadingAllSwaps: false
    }
  },
  computed: {
    isAdmin() {
      return Boolean(this.g.user && (this.g.user.admin || this.g.user.super_user))
    },
    allSwapsColumns() {
      return [
        {name: 'wallet', label: 'Wallet', align: 'left', field: 'wallet'},
        ...this.swapsTable.columns
      ]
    },
    selectedWallet() {
      return this.g.user.wallets.find(w => w.id === this.form.wallet)
    }
  },
  methods: {
    pretty(data) {
      return JSON.stringify(data, null, 2)
    },
    sat(value) {
      return `${LNbits.utils.formatSat(value)} sat`
    },
    amountRule(value) {
      if (!this.terms || !value) return true
      if (value < this.terms.min_swap_amount)
        return `At least ${this.sat(this.terms.min_swap_amount)}`
      if (value > this.terms.max_swap_amount)
        return `At most ${this.sat(this.terms.max_swap_amount)}`
      return true
    },
    statusColor(status) {
      return (
        {success: 'positive', failed: 'negative', unknown: 'warning'}[status] ||
        'primary'
      )
    },
    async getQuote() {
      this.quoting = true
      try {
        const {data} = await LNbits.api.request(
          'GET',
          `/loop/api/v1/quote?amount=${this.form.amount}`,
          this.selectedWallet.inkey
        )
        this.quote = data
        this.quoteDialog.show = true
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.quoting = false
      }
    },
    async startLoopOut() {
      this.starting = true
      try {
        await LNbits.api.request(
          'POST',
          '/loop/api/v1/swaps',
          this.selectedWallet.adminkey,
          {
            amount: this.form.amount,
            address: this.form.address,
            max_total: this.quote.max_total
          }
        )
        this.quoteDialog.show = false
        this.form.amount = null
        this.form.address = ''
        this.$q.notify({type: 'positive', message: 'Loop Out started'})
        await this.getSwaps()
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.starting = false
      }
    },
    async getSwaps() {
      if (!this.g.user.wallets.length) return
      try {
        const {data} = await LNbits.api.request(
          'GET',
          '/loop/api/v1/swaps',
          this.g.user.wallets[0].inkey
        )
        this.swaps = data
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      }
    },
    async getTerms() {
      if (!this.g.user.wallets.length) return
      try {
        const {data} = await LNbits.api.request(
          'GET',
          '/loop/api/v1/terms',
          this.g.user.wallets[0].inkey
        )
        this.terms = data
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      }
    },
    async fetchInfo() {
      this.loadingInfo = true
      try {
        const {data} = await LNbits.api.request('GET', '/loop/api/v1/info')
        this.info = data
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.loadingInfo = false
      }
    },
    toggleInfo() {
      this.showInfo = !this.showInfo
      if (this.showInfo && !this.info) this.fetchInfo()
    },
    async fetchAllSwaps() {
      this.loadingAllSwaps = true
      try {
        const {data} = await LNbits.api.request('GET', '/loop/api/v1/swaps/all')
        this.allSwaps = data
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.loadingAllSwaps = false
      }
    },
    toggleAllSwaps() {
      this.showAllSwaps = !this.showAllSwaps
      if (this.showAllSwaps) this.fetchAllSwaps()
    },
    refreshAdmin() {
      if (this.showInfo) this.fetchInfo()
      if (this.showAllSwaps) this.fetchAllSwaps()
    }
  },
  created() {
    if (this.g.user.wallets.length) {
      this.form.wallet = this.g.user.wallets[0].id
    }
    this.getTerms()
    this.getSwaps()
    // Swap states change on the backend; refresh while the page is open.
    this.pollTimer = setInterval(() => {
      if (this.swaps.some(s => !s.done)) this.getSwaps()
    }, 15000)
  },
  beforeUnmount() {
    clearInterval(this.pollTimer)
  }
}
