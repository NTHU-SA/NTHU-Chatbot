import axios from 'axios'

export default {
  namespaced: true,
  state: {
    loading: false,
    data: [],
    dataCount: 0,
    todayCount: 0, // 今日新增用戶
  },
  getters: {
    getUserData(state) {
      return state.data;
    }
  },
  mutations: {
    SET_LOADING(state, isLoading) {
      state.loading = isLoading;
    },
    SET_USER_DATA(state, options) {
      state.data = options.data;
    },
    SET_USER_DATA_COUNT(state, count) {
      state.dataCount = count;
    },
    SET_USER_NEW_COUNT(state, options) {
      state.todayCount = options.todayCount;
    }
  },
  actions: {
    getUser(context) {
      context.commit('SET_LOADING', true);

      axios.get('/api/v1/admin/userInfo').then((response) => {
        let res = response.data;

        context.commit('SET_USER_DATA', {
          data: res.result
        });

        context.commit('SET_USER_DATA_COUNT', res.result.length);

        context.commit('SET_LOADING', false);
      });
    },
    getNewCount(context) {
      axios.get('/api/v1/admin/userNewCount').then((response) => {
        let res = response.data;

        context.commit('SET_USER_NEW_COUNT', {
          todayCount: res.result.todayCount
        });
      });
    }
  }
}