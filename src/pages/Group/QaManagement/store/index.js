import axios from 'axios'

export default {
  namespaced: true,
  state: {
    loading: false,
    category: [],
    data: []
  },
  mutations: {
    SET_LOADING(state, isLoading) {
      state.loading = isLoading;
    },
    SET_QA_DATA(state, options) {
      state.data = options.data;
      state.category = options.category;
    }
  },
  actions: {
    getAllQA(context) {
      context.commit('SET_LOADING', true);

      axios.get('/api/v1/group/getAllQA').then((response) => {
        let res = response.data;
        
        // 取出category
        let category = [];
        res.result.forEach((item) => {
          category.push(item.category)
        });

        context.commit('SET_QA_DATA', {
          data: res.result,
          category: category
        });

        context.commit('SET_LOADING', false);
      });
    }
  }
}