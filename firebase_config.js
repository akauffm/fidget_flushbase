// Firebase Configuration File for FlushTracker
// Replace the placeholder values below with your Firebase Project credentials
// Obtain these from Firebase Console -> Project Settings -> General -> Your apps -> Web app

const firebaseConfig = {
    apiKey: "YOUR_API_KEY",
    authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
    projectId: "YOUR_PROJECT_ID",
    storageBucket: "YOUR_PROJECT_ID.appspot.com",
    messagingSenderId: "YOUR_SENDER_ID",
    appId: "YOUR_APP_ID"
};

// Optional base tracking URL (defaults to current window location origin + path)
const BASE_TRACKING_URL = window.location.origin + window.location.pathname;
