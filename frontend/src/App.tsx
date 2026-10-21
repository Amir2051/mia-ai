import { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Card, EmptyState, Page } from '@shopify/polaris';
import AppLayout from './components/AppLayout';
import { EMPTY_STATE_IMAGE, PageSkeleton } from './components/states';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const Products = lazy(() => import('./pages/Products'));
const ProductDetail = lazy(() => import('./pages/ProductDetail'));
const Imports = lazy(() => import('./pages/Imports'));
const Orders = lazy(() => import('./pages/Orders'));
const OrderDetail = lazy(() => import('./pages/OrderDetail'));
const Customers = lazy(() => import('./pages/Customers'));
const CustomerDetail = lazy(() => import('./pages/CustomerDetail'));
const Analytics = lazy(() => import('./pages/Analytics'));
const Marketing = lazy(() => import('./pages/Marketing'));
const Settings = lazy(() => import('./pages/Settings'));
const Reports = lazy(() => import('./pages/Reports'));
const Mia = lazy(() => import('./pages/Mia'));
const Social = lazy(() => import('./pages/Social'));
const Login = lazy(() => import('./pages/Login'));

function NotFound() {
  return (
    <Page>
      <Card>
        <EmptyState
          heading="This page doesn't exist"
          action={{ content: 'Go to dashboard', url: '/dashboard' }}
          image={EMPTY_STATE_IMAGE}
        >
          <p>Check the address, or pick a page from the Mia menu in your Shopify sidebar.</p>
        </EmptyState>
      </Card>
    </Page>
  );
}

export default function App() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route element={<AppLayout />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/mia" element={<Mia />} />
          <Route path="/products" element={<Products />} />
          <Route path="/products/:id" element={<ProductDetail />} />
          <Route path="/imports" element={<Imports />} />
          <Route path="/orders" element={<Orders />} />
          <Route path="/orders/:id" element={<OrderDetail />} />
          <Route path="/customers" element={<Customers />} />
          <Route path="/customers/:id" element={<CustomerDetail />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/marketing" element={<Marketing />} />
          <Route path="/social" element={<Social />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
