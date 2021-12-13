<template>
<div>
  <v-container class="pt-0 pb-8">
    <section class="group-user-management">
      <div class="user-dashboard title">
        <v-row>
          <v-col class="dashboard-item" md="6" sm="12">
            <div class="item-number">{{ this.$store.state.userManagement.dataCount }}</div>
            <div class="item-desc"><v-icon>person</v-icon> 用戶數量</div>
          </v-col>
          <v-col class="dashboard-item" md="6" sm="12">
            <div class="item-number">{{ this.$store.state.userManagement.todayCount }}</div>
            <div class="item-desc"><v-icon>group_add</v-icon> 今日新增</div>
          </v-col>
        </v-row>
      </div>

      <v-data-table
        class="mt-3"
        :headers="headers"
        :items="userTableData"
        item-key="userID"
        :loading="this.$store.state.userManagement.loading"
      >
      </v-data-table>
    </section>
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
        { text: '註冊日期', value: 'time' },
        { 
          text: 'Line ID',
          align: 'left',
          sortable: false,
          value: 'userID',
        }
      ],
    }
  },
  computed: {
    userTableData() {
      return this.$store.state.userManagement.data;
    }
  },
  mounted() {
    this.dispatchUserManagement();
  },
  methods: {
    dispatchUserManagement() {
      this.$store.dispatch('userManagement/getUser');
      this.$store.dispatch('userManagement/getNewCount');
    },
    // deleteConfirm(id) {
    //   let self = this;
    //   this.$store.dispatch('confirmDialog/openDialog', {
    //     title: '刪除趣聞',
    //     msg: '是否刪除這則趣聞，刪除後無法復原',
    //     delItemID: id,
    //     delApi: '/api/v1/group/delAnecdote',
    //     resetTarget: 'anecdote/getAnecdote'
    //   });
    // }
  }
}
</script>

<style lang="scss">
.group-user-management {
  .user-dashboard {
    margin-bottom: 25px;
    margin-top: 15px;
    background-color: rgba(0, 0, 0, .1);
    border-radius: 15px;

    .dashboard-item {
      text-align: center;
      margin-bottom: 15px;
      margin-top: 15px;

      .item-number {
        font-size: 35px;
        font-weight: bold;
        margin-bottom: 10px;
      }

      .item-desc {
        font-size: 14px;
      }

      .item-desc .v-icon {
        font-size: 16px;
      }
    }
  }
}

</style>

