import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '看板' },
      },
      {
        path: 'upstream-keys',
        name: 'upstream-keys',
        meta: { requiresAuth: true },
        component: () => import('@/views/UpstreamKeysView.vue'),
      },
      {
        path: 'keys',
        name: 'keys',
        component: () => import('@/views/KeysView.vue'),
        meta: { title: 'API Key 管理' },
      },
      {
        path: 'requests',
        name: 'requests',
        component: () => import('@/views/RequestsView.vue'),
        meta: { title: '审计检索' },
      },
      {
        path: 'usage',
        name: 'usage',
        component: () => import('@/views/UsageView.vue'),
        meta: { title: '费用看板' },
      },
      {
        path: 'pricing',
        name: 'pricing',
        component: () => import('@/views/PricingView.vue'),
        meta: { title: '定价规则' },
      },
      {
        path: 'audit-logs',
        name: 'audit-logs',
        component: () => import('@/views/AuditLogsView.vue'),
        meta: { title: '管理操作日志' },
      },
    ],
  },
  {
    // portal 路由不受管理台守卫约束：API Key 登录态由 portalHttp 的 401 拦截器负责
    path: '/portal/login',
    name: 'portal-login',
    meta: { public: true, title: '用户中心登录' },
    component: () => import('@/views/portal/PortalLoginView.vue'),
  },
  {
    path: '/portal',
    meta: { public: true },
    component: () => import('@/layouts/PortalLayout.vue'),
    children: [
      {
        path: 'dashboard',
        name: 'portal-dashboard',
        meta: { public: true, title: '我的概览' },
        component: () => import('@/views/portal/PortalDashboardView.vue'),
      },
      {
        path: 'chat',
        name: 'portal-chat',
        meta: { public: true, title: '对话' },
        component: () => import('@/views/portal/PortalChatView.vue'),
      },
      {
        path: 'requests',
        name: 'portal-requests',
        meta: { public: true, title: '请求记录' },
        component: () => import('@/views/portal/PortalRequestsView.vue'),
      },
      {
        path: 'usage',
        name: 'portal-usage',
        meta: { public: true, title: '消耗统计' },
        component: () => import('@/views/portal/PortalUsageView.vue'),
      },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isAuthenticated) {
    return { name: 'login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  }
  if (to.name === 'login' && auth.isAuthenticated) {
    return { name: 'dashboard' }
  }
  return true
})
