import axios from 'axios'

export default {
  namespaced: true,
  state: {
    // visible
    loading: true,

    categoryItems: ['演講訊息', '藝術展覽', '各類活動'],

    // data
    newsData: []
  },
  mutations: {
    // visible
    SET_LOADING(state, isLoading) {
      state.loading = isLoading;
    },
    // data
    SET_DATA(state, data) {
      state.newsData = data;
    }
  },
  actions: {
    // Data
    getNewsData(context) {
      context.commit('SET_LOADING', true);

      axios.get('/api/v1/instanews/news', {
        params: {
          category: 'all'
        }
      }).then((response) => {
        let res = response.data;
        context.commit('SET_DATA', res.result);

        context.commit('SET_LOADING', false);
      }).catch((error) => {
        console.log(error);
      });
    }
  }
}