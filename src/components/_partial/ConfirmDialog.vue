<template>
<div>
  <v-dialog v-model="visible" max-width="290">
    <v-card>
      <v-card-title class="headline">{{ this.$store.state.confirmDialog.title }}</v-card-title>

      <v-card-text>
        {{ this.$store.state.confirmDialog.msg }}
      </v-card-text>

      <v-card-actions>
        <v-spacer></v-spacer>

        <v-btn color="red darken-1" text @click="close">
          Cancel
        </v-btn>

        <v-btn color="primary" text @click="deleteItem">
          Ok
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</div>
</template>

<script>
import axios from 'axios'

export default {
  components: {},
  data() {
    return {}
  },
  computed: {
    visible() {
      return this.$store.state.confirmDialog.visible
    }
  },
  methods: {
    close() {
      this.$store.commit('confirmDialog/CLOSE_DIALOG');
    },
    deleteItem() {
      axios.post(this.$store.state.confirmDialog.delApi, {
        id: this.$store.state.confirmDialog.delItemID
      }).then((response) => {
        let res = response.data;
        // console.log(res);

        this.$store.dispatch('snackbar/openSnackbar', {
          msg: '刪除成功!'
        });

        // console.log(this.$store.state.confirmDialog.resetTarget);
        this.$store.dispatch(this.$store.state.confirmDialog.resetTarget);
      }).catch((error) => {
        this.$store.dispatch('snackbar/openSnackbar', {
          msg: error.response.data.msg
        });
      });

      this.$store.commit('confirmDialog/CLOSE_DIALOG');
    }
  }
}
</script>