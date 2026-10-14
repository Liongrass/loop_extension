window.PageLoop = {
  template: '#page-loop',
  data() {
    return {
      info: null,
      terms: null,
      loading: false
    }
  },
  computed: {
    isAdmin() {
      return Boolean(this.g.user && (this.g.user.admin || this.g.user.super_user))
    }
  },
  methods: {
    pretty(data) {
      return JSON.stringify(data, null, 2)
    },
    async fetchLoop() {
      this.loading = true
      try {
        const [info, terms] = await Promise.all([
          LNbits.api.request('GET', '/loop/api/v1/info'),
          LNbits.api.request('GET', '/loop/api/v1/terms')
        ])
        this.info = info.data
        this.terms = terms.data
      } catch (error) {
        LNbits.utils.notifyApiError(error)
      } finally {
        this.loading = false
      }
    }
  },
  created() {
    if (this.isAdmin) {
      this.fetchLoop()
    }
  }
}
