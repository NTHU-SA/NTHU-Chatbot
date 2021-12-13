<template>
  <v-app id="instanews-wrapper">
    <v-navigation-drawer v-model="drawer" app clipped>
      <v-list dense>
        <!-- <v-list-item link to="/instanews/push">
          <v-list-item-action>
            <v-icon>message</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>推播新聞</v-list-item-title>
          </v-list-item-content>
        </v-list-item> -->
        <v-list-item link to="/instanews/post">
          <v-list-item-action>
            <v-icon>library_books</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>新增新聞</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/instanews/wall">
          <v-list-item-action>
            <v-icon>dashboard</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>新聞牆</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/instanews/epidemicData">
          <v-list-item-action>
            <v-icon>bug_report</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>疫情資料</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
      </v-list>
    </v-navigation-drawer>

    <v-app-bar app clipped-left>
      <v-app-bar-nav-icon @click.stop="drawer=!drawer" />
      <!-- <img style="width: 25px; margin-right: 10px;" src="../../assets/logo/logo.png"> -->
      <span style="font-weight: bold;">沐報 Instanews</span>
      <v-spacer></v-spacer>
      <v-btn @click="logout">Sign out</v-btn>
    </v-app-bar>

    <v-content>
      <v-container class="pa-6">
        <router-view></router-view>
      </v-container>
    </v-content>

    <v-footer app>
      <span>&copy; 2019 &nbsp; NTHU Chatbot</span>
    </v-footer>
  </v-app>
</template>

<script>
import axios from 'axios'

export default {
  data: () => ({
    drawer: null
  }),
  created () {
    this.$vuetify.theme.dark = true;
  },
  mounted() {
    this.checkLogin();
  },
  methods: {
    checkLogin() {
      axios.get('/api/v1/checkLogin').then((response) => {
        let res = response.data;

        if (res.result.department == 'instanews') {
          // pass
        } else {
          this.$router.push('/');
        }
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '登入過期了，請重新登入！'
        });
        this.$router.push('/');
      });
    },
    logout() {
      axios.post('/api/v1/logout').then((response) => {
        let res = response.data;
        this.$router.push('/');
      }).catch((error) => {
        console.log(error);
      });
    },
  }
}
</script>
