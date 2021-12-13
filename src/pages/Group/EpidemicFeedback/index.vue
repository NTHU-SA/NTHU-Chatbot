<template>
<div>
  <v-container class="pt-0 pb-8">
    <v-data-table
      :headers="headers"
      :items="feedbackTableData"
      item-key="id"
      class="elevation-1"
      :loading="this.$store.state.epidemicfeedback.loading"
    >
      <template v-slot:item.action="{ item }">
        <v-icon small @click="deleteConfirm(item.id)">delete</v-icon>
      </template>
    </v-data-table>
  </v-container>
</div>
</template>

<script>
import axios from 'axios'

export default {
  components: {},
  data() {
    return {
      headers: [
        { text: '日期', value: 'time' },
        { 
          text: 'Line ID',
          align: 'left',
          sortable: false,
          value: 'userID',
        },
        { text:'回饋內容', value:'content' },
        { text: '操作', value: 'action', sortable: false },
      ],
      // feedbackTableData: []
    }
  },
  computed: {
    feedbackTableData() {
      return this.$store.state.epidemicfeedback.data;
    }
  },
  mounted() {
    this.dispatchFeedback();
  },
  methods: {
    dispatchFeedback() {
      this.$store.dispatch('epidemicfeedback/getFeedback');
    },
    deleteConfirm(id) {
      let self = this;
      this.$store.dispatch('confirmDialog/openDialog', {
        title: '刪除回饋',
        msg: '是否刪除這則回饋，刪除後無法復原',
        delItemID: id,
        delApi: '/api/v1/group/delFeedback',
        resetTarget: 'epidemicfeedback/getFeedback'
      });
    }
  }
}
</script>

