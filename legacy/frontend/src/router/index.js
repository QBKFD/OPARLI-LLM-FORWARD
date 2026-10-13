import { createWebHistory, createRouter } from 'vue-router';

import LiveTradingView from '../views/LiveTradingView.vue';
import LiveDevView from '../views/LiveDevView.vue';
import BacktestView from '../views/BacktestView.vue';
import ValidationView from '../views/ValidationView.vue';
import AboutView from '../views/AboutView.vue';
import LoginView from '../views/LoginView.vue';
import DashboardView from '../views/DashboardView.vue';

const routes = [
    { path: '/', redirect: '/live' },
    { path: '/live', component: LiveTradingView },
    { path: '/validation', component: ValidationView },  // Validation hub page
    { path: '/backtest', redirect: '/validation' },  // Legacy redirect
    { path: '/about', component: AboutView },
    {
        path: '/login',
        component: LoginView,
        meta: { guest: true }  // Only accessible when NOT logged in
    },
    {
        path: '/dashboard',
        component: DashboardView,
        meta: { requiresAuth: true }  // Requires authentication
    },
    {
        path: '/live-dev',
        component: LiveDevView,
        meta: { requiresAuth: true }  // Protected dev version
    },
];

const router = createRouter({
    history: createWebHistory(),
    routes,
});

// Navigation guard
router.beforeEach(async (to, from, next) => {
    const token = localStorage.getItem('dashboard_token');

    // Check if route requires authentication
    if (to.meta.requiresAuth) {
        if (!token) {
            // No token, redirect to login
            return next('/login');
        }

        // Verify token is still valid
        try {
            const { useAuthStore } = await import('../stores/authStore');
            const authStore = useAuthStore();
            const isValid = await authStore.verifyToken();

            if (!isValid) {
                return next('/login');
            }
        } catch (err) {
            console.error('Auth check failed:', err);
            return next('/login');
        }
    }

    // Check if route is guest-only (login page)
    if (to.meta.guest && token) {
        // Already logged in, redirect to dashboard
        return next('/dashboard');
    }

    next();
});

export default router;
