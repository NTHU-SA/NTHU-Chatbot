<template>
  <v-app id="group-wrapper">
    <v-navigation-drawer v-model="drawer" app clipped>
      <v-list dense>
        <v-subheader>Official</v-subheader>
        <v-list-item link to="/group/userManagement">
          <v-list-item-action>
            <v-icon>person</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>學生管理</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/group/qaManagement">
          <v-list-item-action>
            <v-icon>question_answer</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>問答管理</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/group/push">
          <v-list-item-action>
            <v-icon>message</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>推播訊息</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/group/feedback">
          <v-list-item-action>
            <v-icon>feedback</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>意見回饋</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/group/epidemicData">
          <v-list-item-action>
            <v-icon>bug_report</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>疫情資料</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/group/epidemicfeedback">
          <v-list-item-action>
            <v-icon>live_help</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>疫情提問</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/group/anecdote">
          <v-list-item-action>
            <v-icon>emoji_nature</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>校園趣聞</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
        <v-list-item link to="/group/wall">
          <v-list-item-action>
            <v-icon>dashboard</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>新聞牆</v-list-item-title>
          </v-list-item-content>
        </v-list-item>

        <v-subheader>Beta</v-subheader>
        <v-list-item link to="/group/betapush">
          <v-list-item-action>
            <v-icon>build</v-icon>
          </v-list-item-action>
          <v-list-item-content>
            <v-list-item-title>Beta 推播訊息</v-list-item-title>
          </v-list-item-content>
        </v-list-item>
      </v-list>
    </v-navigation-drawer>

    <v-app-bar app clipped-left>
      <v-app-bar-nav-icon @click.stop="drawer=!drawer" />
      <!-- <img style="width: 25px; margin-right: 10px;" src="../../assets/logo/logo.png"> -->
      <span style="font-weight: bold;">清大校園情報員後勤</span>
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

        if (res.result.department == 'nthuchatbot') {
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
