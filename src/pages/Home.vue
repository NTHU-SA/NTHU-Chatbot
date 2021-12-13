<template>
<div @keyup.enter="login">
<v-app>
  <v-content>
    <v-container class="home-wrapper fill-height" fluid>
      <v-row align="center" justify="center">
        <v-col cols="12" sm="8" md="4">
          <v-card class="elevation-12">
            <v-toolbar color="#EB5F69" dark flat>
              <v-toolbar-title>清大 Chatbot 後台系統</v-toolbar-title>
              <v-spacer />
              
            </v-toolbar>
            <v-card-text>
              <v-form>
                <v-text-field label="Login" name="login" prepend-icon="person" type="text" v-model="account" />
                <v-text-field label="Password" name="password" prepend-icon="lock" type="password" v-model="password"/>
              </v-form>
            </v-card-text>
            <v-card-actions>
              <v-spacer />
              <v-btn color="#EB5F69" dark @click="login">Login</v-btn>
            </v-card-actions>
          </v-card>
        </v-col>
      </v-row>
    </v-container>
  </v-content>
</v-app>
</div>
</template>

<script>
import axios from 'axios'

export default {
  props: {
    source: String,
  },
  data() {
    return {
      account: '',
      password: '',
    }
  },
  created () {
    this.$vuetify.theme.dark = false;
  },
  mounted() {
    this.checkLogin();
  },
  methods: {
    login() {
      axios.post('/api/v1/login', {
        'account': this.account,
        'password': this.password
      }).then((response) => {
        let res = response.data;

        if (res.result.department == 'instanews') {
          this.$router.push('/instanews/post');
        } else if (res.result.department == 'nthuchatbot') {
          this.$router.push('/group/userManagement');
        }
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });
    },
    checkLogin() {
      axios.get('/api/v1/checkLogin').then((response) => {
        let res = response.data;

        if (res.result.department == 'instanews') {
          this.$router.push('/instanews/post');
        } else if (res.result.department == 'nthuchatbot') {
          this.$router.push('/group/userManagement');
        }
      }).catch((error) => {
        console.log(error.response.data.msg);
      });
    }
  }
}
</script>

<style lang="scss">
  .home-wrapper {
    background-image: url("../assets/home/cover.svg");
    background-size: cover;
    background-position: center;
    // background-color: #424242;
  }
</style>