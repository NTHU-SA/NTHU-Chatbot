import Vue from 'vue'
import VueRouter from 'vue-router'

import Home from '../pages/Home.vue'

import CommonNewsWall from '@/components/Common/NewsWall'

import GroupWrapper from '@/components/Group/GroupWrapper'
import GroupUserManagement from '@/pages/Group/UserManagement'
import GroupQaManagement from '@/pages/Group/QaManagement'
import GroupPush from '@/pages/Group/Push'
import GroupFeedback from '@/pages/Group/Feedback'
import GroupEpidemicData from '@/pages/Group/EpidemicData'
import GroupEpidemicFeedback from '@/pages/Group/EpidemicFeedback'
import GroupAnecdote from '@/pages/Group/Anecdote'
import GroupBetaPush from '@/pages/Group/BetaPush'

import InstanewsWrapper from '@/components/Instanews/InstanewsWrapper'
import InstanewsPush from '@/pages/Instanews/Push'
import InstanewsPost from '@/pages/Instanews/Post'
// import InstanewsWall from '@/pages/Instanews/Wall'
import InstanewsEpidemicData from '@/pages/Instanews/EpidemicData'

Vue.use(VueRouter)

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home
  },
  {
    path: '/group',
    component: GroupWrapper,
    children: [{
      path: 'userManagement',
      name: 'GroupUserManagement',
      component: GroupUserManagement
    }, {
      path: 'qaManagement',
      name: 'GroupQaManagement',
      component: GroupQaManagement
    }, {
      path: 'push',
      name: 'GroupPush',
      component: GroupPush
    }, {
      path: 'feedback',
      name: 'GroupFeedback',
      component: GroupFeedback
    }, {
      path: 'epidemicData',
      name: 'GroupEpidemicData',
      component: GroupEpidemicData
    }, {
      path: 'epidemicfeedback',
      name: 'GroupEpidemicFeedback',
      component: GroupEpidemicFeedback
    }, {
      path: 'anecdote',
      name: 'GroupAnecdote',
      component: GroupAnecdote
    }, {
      path: 'wall',
      component: CommonNewsWall
    }, {
      path: 'betapush',
      name: 'GroupBetaPush',
      component: GroupBetaPush
    }]
  },
  {
    path: '/instanews',
    component: InstanewsWrapper,
    children: [{
      path: 'push',
      name: 'InstanewsPush',
      component: InstanewsPush
    }, {
      path: 'post',
      name: 'InstanewsPost',
      component: InstanewsPost
    }, {
      path: 'wall',
      component: CommonNewsWall
    }, {
      path: 'epidemicData',
      name: 'InstanewsEpidemicData',
      component: InstanewsEpidemicData
    }]
  }
]

const router = new VueRouter({
  mode: 'history',
  base: process.env.BASE_URL,
  routes
})

export default router
