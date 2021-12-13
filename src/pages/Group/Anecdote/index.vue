<template>
<div>
  <v-container class="pt-0 pb-8">
    <v-data-table
      :headers="headers"
      :items="anecdoteTableData"
      item-key="id"
      class="elevation-1"
      :loading="this.$store.state.anecdote.loading"
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
    }
  },
  computed: {
    anecdoteTableData() {
      return this.$store.state.anecdote.data;
    }
  },
  mounted() {
    this.dispatchAnecdote();
  },
  methods: {
    dispatchAnecdote() {
      this.$store.dispatch('anecdote/getAnecdote');
    },
    deleteConfirm(id) {
      let self = this;
      this.$store.dispatch('confirmDialog/openDialog', {
        title: '刪除趣聞',
        msg: '是否刪除這則趣聞，刪除後無法復原',
        delItemID: id,
        delApi: '/api/v1/group/delAnecdote',
        resetTarget: 'anecdote/getAnecdote'
      });
    }
  }
}
</script>

